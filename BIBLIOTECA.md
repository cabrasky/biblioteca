# biblioteca

Fork de [Calibre-Web](https://github.com/janeczku/calibre-web) con el despliegue de la biblioteca
del homelab: **https://libros.cabrasky.net**. Login con **FreeIPA** (vía Authelia), en **español**,
y una pestaña **Peticiones** para pedir libros que se descargan con **aMule** y entran solos en la
biblioteca.

| URL | Qué es | Quién entra |
|-----|--------|-------------|
| https://libros.cabrasky.net/ | Calibre-Web (navegar, leer, descargar, editar, OPDS en `/opds`) | cualquier usuario de FreeIPA; admins: grupo `admins` + `cabrasky` |
| https://libros.cabrasky.net/peticiones | pestaña Peticiones (buscar → «Pedir») | cualquier usuario de FreeIPA |
| https://emule.cabrasky.net/ | interfaz web de aMule (amuleapi) | grupo `admins`, solo LAN/VPN |

`libros` (y el portal de login `ipca`) son accesibles desde Internet; `emule` solo desde LAN/VPN.

## Qué cambia respecto a Calibre-Web original

Todos los cambios en el código llevan la marca `[biblioteca]`:

- **`cps/proxy_provisioning.py`** (nuevo) + `cps/usermanagement.py`: con la autenticación por
  cabecera del proxy activada, los usuarios de FreeIPA se **crean solos** la primera vez que entran
  (roles, idioma y visibilidad por defecto de la configuración) y su rol de **administrador se
  sincroniza** con el grupo del proxy. Variables: `CALIBRE_WEB_PROXY_AUTOCREATE`,
  `CALIBRE_WEB_PROXY_ADMIN_GROUP`, `CALIBRE_WEB_PROXY_ADMIN_USERS`, `CALIBRE_WEB_PROXY_GROUPS_HEADER`,
  `CALIBRE_WEB_PROXY_EMAIL_HEADER`. El OPDS acepta la cabecera del proxy aunque la petición traiga
  `Authorization` (los lectores OPDS se autentican contra Authelia con usuario y contraseña de FreeIPA).
- **`cps/peticiones.py`** (nuevo), plantilla `peticiones.html` (ambos temas),
  `static/js/peticiones.js`, `static/css/peticiones.css`, entrada en el menú lateral
  (`cps/render_template.py`) y registro del blueprint (`cps/main.py`): pestaña **Peticiones**.
  Solo aparece si `CALIBRE_WEB_PETICIONES_API` está definida; habla con la API del servicio
  `pedidos/`.
- **`Dockerfile`**, **`biblioteca/`**: imagen con Calibre-Web + binarios oficiales de calibre
  (conversión), `configurar.py` (configuración gestionada: español, título «Biblioteca», biblioteca
  `/biblioteca`, login por cabecera `Remote-User` solo desde la red de pods, binarios de calibre,
  subidas activadas; contraseña aleatoria para el `admin` local) e `importador.py`.
- **`pedidos/`**: API (FastAPI) que busca en eD2K/Kad y pide descargas a aMule (amuleapi).
- **`k8s/`, `argocd/`, `Jenkinsfile`**: despliegue GitOps.

### Actualizar desde upstream

```sh
git remote add upstream https://github.com/janeczku/calibre-web.git   # una vez
git fetch upstream && git merge upstream/master                        # en main
```

La rama `master` sigue a upstream sin cambios; todo lo nuestro está en `main` (rama por defecto).

## Cómo funciona

```
navegador ─▶ nginx .21 (TLS) ─▶ Traefik :443 ─▶ Authelia (forward-auth, LDAP FreeIPA)
                                     ├─ libros.cabrasky.net/         ─▶ Calibre-Web :8083 (Remote-User)
                                     ├─ libros.cabrasky.net/pedidos  ─▶ pedidos :8000 ─▶ amuleapi :4711
                                     └─ emule.cabrasky.net           ─▶ amuleapi :4711 (UI web)

aMule ──descarga──▶ PVC descargas/incoming ──▶ importador (sidecar de Calibre-Web)
                                                  └─ calibredb add sobre /biblioteca
```

- Calibre-Web confía en `Remote-User` solo si la petición viene de la red de pods
  (`10.42.0.0/16`) y una **NetworkPolicy** deja que solo Traefik hable con Calibre-Web y pedidos:
  la cabecera no se puede falsificar.
- **importador**: cada 20 s mira `incoming/`; un libro sin cambios durante 30 s se añade con la
  etiqueta `pedido:<usuario>` y se borra de `incoming/`. Duplicados se descartan; lo que calibre
  rechaza va a `fallidos/`.
- Límite: 10 pedidos/día por usuario (los `admins` sin límite).

## Despliegue (GitOps)

- Jenkins (`biblioteca`) construye y publica `registry.cabrasky.net/biblioteca-calibre-web` y
  `biblioteca-pedidos` (`:latest` y `:<sha>`), con smoke test. No despliega.
- Argo CD (`argocd/biblioteca.yaml`) sincroniza `k8s/` (rama `main`) al namespace `biblioteca`.
- Image Updater (`argocd/biblioteca-image-updater.yaml`) sigue el digest de `:latest`.
- aMule va fijado (`ngosang/amule:3.1.0-1`): se actualiza a mano en `k8s/amule.yaml`.

### Secret (fuera de Git)

```sh
kubectl -n biblioteca create secret generic biblioteca-secrets \
  --from-literal=AMULE_GUI_PWD="$(openssl rand -hex 16)" \
  --from-literal=AMULE_WEBUI_PWD="$(openssl rand -hex 16)"
```

`AMULE_WEBUI_PWD` es la contraseña de la UI de `emule.cabrasky.net` (segundo login, después de
Authelia; **no** es la de FreeIPA):
`kubectl -n biblioteca get secret biblioteca-secrets -o jsonpath='{.data.AMULE_WEBUI_PWD}' | base64 -d`.
La imagen de aMule la escribe en sus logs al arrancar.

### Puertos P2P

aMule publica en el nodo k3s (`192.168.1.22`, `hostPort`) **4662/TCP**, **4665/UDP** y
**4672/UDP**. Sin redirigirlos en el router funciona con *LowID* (más lento).

### Lectores OPDS (KOReader, Moon+ Reader…)

Catálogo `https://libros.cabrasky.net/opds`, usuario y contraseña de FreeIPA.

## Almacenamiento

PVC `local-path`: `calibre-biblioteca` (biblioteca de calibre, `metadata.db` + ficheros; **nunca**
en NFS/SMB), `calibre-web-config` (`app.db` de Calibre-Web: usuarios, estanterías, ajustes),
`descargas` (aMule, importador y pedidos) y `amule-config`.

## Desarrollo local

```sh
docker build -t biblioteca-calibre-web:dev .
docker run --rm -p 8083:8083 -e CW_PROXIES_CONFIANZA=0.0.0.0/0 -e CALIBRE_WEB_PROXY_AUTOCREATE=1 \
  -e CALIBRE_WEB_PETICIONES_API=/pedidos/api biblioteca-calibre-web:dev
# y manda la cabecera Remote-User: <usuario> (p. ej. con una extensión del navegador)
```
