"""Pedidos de libros: busca en la red eD2K/Kad (aMule) y descarga a la biblioteca de calibre.

La identidad del usuario llega de Authelia (FreeIPA) en las cabeceras Remote-*;
Traefik las sobrescribe siempre, así que no se pueden falsificar desde el navegador.
Las descargas terminadas las importa a calibre el contenedor «importador» del pod
de calibre, que lee/escribe el estado compartido en /descargas/.biblioteca.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import os
import re
import sqlite3
import time
from contextlib import asynccontextmanager
from pathlib import Path

import httpx
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import ebiblio

AMULE_URL = os.environ.get("AMULE_URL", "http://amule:4711").rstrip("/")
AMULE_PWD = os.environ.get("AMULE_WEBUI_PWD", "")
ESTADO = Path(os.environ.get("ESTADO", "/descargas/.biblioteca"))
INCOMING = Path(os.environ.get("INCOMING", "/descargas/incoming"))
DB_PATH = Path(os.environ.get("DB_PATH", str(ESTADO / "pedidos.db")))
CALIBRE_PUBLICO = os.environ.get("CALIBRE_PUBLICO", "/")
# Enlace a la ficha del libro en la web de la biblioteca (Calibre-Web: /book/<id>)
ENLACE_LIBRO = os.environ.get("ENLACE_LIBRO", "/book/{id}")
# La interfaz de usuario es la pestaña «Peticiones» de Calibre-Web; /pedidos/ redirige allí
INTERFAZ = os.environ.get("INTERFAZ", "/peticiones")
GRUPO_ADMIN = os.environ.get("GRUPO_ADMIN", "admins")
BASE = os.environ.get("BASE_PATH", "/pedidos").rstrip("/")
MAX_ACTIVOS_POR_USUARIO = int(os.environ.get("MAX_ACTIVOS_POR_USUARIO", "10"))

EXTENSIONES = {
    "epub", "kepub", "mobi", "azw", "azw3", "azw4", "kfx", "pdf", "fb2", "fbz",
    "djvu", "cbz", "cbr", "cb7", "lit", "lrf", "pdb", "rtf", "docx", "odt",
}
HASH_RE = re.compile(r"^[0-9a-fA-F]{32}$")
STATIC = Path(__file__).parent / "static"


# ---------------------------------------------------------------- aMule (amuleapi)
class Amule:
    def __init__(self) -> None:
        self.cliente = httpx.AsyncClient(base_url=AMULE_URL + "/api/v1", timeout=20)
        self.token: str | None = None
        self.lock = asyncio.Lock()

    async def _login(self) -> None:
        r = await self.cliente.post("/auth/login", params={"include_token": "true"}, json={"password": AMULE_PWD})
        if r.status_code != 200:
            raise HTTPException(502, f"aMule rechazó el login ({r.status_code})")
        self.token = r.json()["token"]

    async def pedir(self, metodo: str, ruta: str, **kw) -> httpx.Response:
        for intento in range(2):
            async with self.lock:
                if not self.token:
                    await self._login()
                token = self.token
            try:
                r = await self.cliente.request(metodo, ruta, headers={"Authorization": f"Bearer {token}"}, **kw)
            except httpx.HTTPError as e:
                raise HTTPException(503, f"aMule no responde: {e.__class__.__name__}") from e
            if r.status_code == 401 and intento == 0:
                self.token = None
                continue
            return r
        return r

    async def json(self, metodo: str, ruta: str, ok=(200, 202), **kw):
        r = await self.pedir(metodo, ruta, **kw)
        if r.status_code not in ok:
            try:
                msg = r.json()["error"]["message"]
            except Exception:
                msg = r.text[:200]
            raise HTTPException(502 if r.status_code < 500 else 503, f"aMule: {msg}")
        return r.json() if r.content else {}


amule = Amule()


# ---------------------------------------------------------------- base de datos
def db() -> sqlite3.Connection:
    c = sqlite3.connect(DB_PATH, timeout=10)
    c.row_factory = sqlite3.Row
    return c


def init_db() -> None:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    (ESTADO / "pedidos").mkdir(parents=True, exist_ok=True)
    with db() as c:
        c.execute(
            """CREATE TABLE IF NOT EXISTS pedidos (
                 hash TEXT PRIMARY KEY, nombre TEXT NOT NULL, tamano INTEGER,
                 usuario TEXT NOT NULL, nombre_usuario TEXT, creado REAL NOT NULL,
                 cancelado REAL)"""
        )


@asynccontextmanager
async def vida(_: FastAPI):
    init_db()
    yield
    await amule.cliente.aclose()
    await ebiblio.cerrar()


app = FastAPI(title="Pedidos de libros", lifespan=vida, docs_url=None, redoc_url=None, openapi_url=None)


# ---------------------------------------------------------------- utilidades
def usuario(req: Request) -> dict:
    u = req.headers.get("remote-user") or os.environ.get("USUARIO_DEV") or ""
    if not u:
        raise HTTPException(401, "Sin usuario (¿falta Authelia delante?)")
    grupos = [g.strip() for g in req.headers.get("remote-groups", "").split(",") if g.strip()]
    return {
        "usuario": u,
        "nombre": req.headers.get("remote-name") or u,
        "admin": GRUPO_ADMIN in grupos or os.environ.get("USUARIO_DEV_ADMIN") == "1",
    }


def extension(nombre: str) -> str:
    return nombre.rsplit(".", 1)[-1].lower() if "." in nombre else ""


def clave(nombre: str) -> str:
    return hashlib.sha1(nombre.encode("utf-8")).hexdigest()


def importados() -> dict[str, dict]:
    """Último evento del importador por nombre de fichero y por hash."""
    eventos: dict[str, dict] = {}
    f = ESTADO / "importados.jsonl"
    if not f.exists():
        return eventos
    with open(f, encoding="utf-8") as fh:
        for linea in fh:
            try:
                e = json.loads(linea)
            except ValueError:
                continue
            eventos[e.get("nombre", "")] = e
            if e.get("hash"):
                eventos[e["hash"].lower()] = e
    return eventos


def enlace_libro(book_id: int) -> str:
    return ENLACE_LIBRO.format(id=book_id)


# ---------------------------------------------------------------- API
api = "/api"


@app.get(BASE + "/healthz")
async def healthz():
    return {"ok": True}


@app.get(BASE + api + "/yo")
async def yo(req: Request):
    u = usuario(req)
    return {**u, "biblioteca": CALIBRE_PUBLICO}


@app.get(BASE + api + "/estado")
async def estado(req: Request):
    usuario(req)
    s = await amule.json("GET", "/status")
    ed2k, kad = s.get("ed2k") or {}, s.get("kad") or {}
    return {
        "ed2k": ed2k.get("state"),
        "kad": kad.get("state"),
        "high_id": ed2k.get("high_id"),
        "velocidad": (s.get("speeds") or {}).get("download_speed_bytes_per_second", 0),
        "libre": (s.get("disk") or {}).get("incoming_free_bytes"),
    }


@app.get(BASE + api + "/ebiblio")
async def buscar_ebiblio(q: str, req: Request):
    """Préstamo gratuito antes que aMule: ¿está el libro en eBiblio?
    recomendar=True si hay alguno que se pueda prestar o reservar ya."""
    usuario(req)
    q = q.strip()[:120]
    if not ebiblio.activo() or len(q) < 3:
        return {"activo": ebiblio.activo(), "nombre": ebiblio.EBIBLIO_NOMBRE, "resultados": [], "recomendar": False}
    res = await ebiblio.buscar(q)
    return {
        "activo": True,
        "nombre": ebiblio.EBIBLIO_NOMBRE,
        "url": ebiblio.EBIBLIO_URL,
        "resultados": res,
        "recomendar": any(r["estado"] in ("disponible", "reservable") for r in res),
    }


@app.get(BASE + api + "/ebiblio/portada")
async def portada_ebiblio(u: str, req: Request):
    usuario(req)
    r = await ebiblio.portada(u)
    if not r:
        raise HTTPException(404, "Portada no disponible")
    return Response(content=r[0], media_type=r[1], headers={"Cache-Control": "private, max-age=86400"})


class Busqueda(BaseModel):
    q: str = Field(min_length=3, max_length=120)


@app.post(BASE + api + "/busquedas")
async def buscar(b: Busqueda, req: Request):
    """Lanza la búsqueda en eD2K y en Kad a la vez. aMule ≥3.2 acepta type=all;
    en 3.1 hay que lanzar dos búsquedas y fusionar los resultados por hash."""
    usuario(req)
    q = b.q.strip()
    r = await amule.pedir("POST", "/search", json={"query": q, "type": "all"})
    if r.status_code in (200, 202):
        return {"id": str(r.json()["search_id"])}
    ids = []
    for tipo in ("global", "kad"):
        r = await amule.pedir("POST", "/search", json={"query": q, "type": tipo})
        if r.status_code in (200, 202):
            ids.append(str(r.json()["search_id"]))
    if not ids:
        raise HTTPException(502, f"aMule no pudo buscar: {r.text[:200]}")
    return {"id": "-".join(ids)}


def _ids(sid: str) -> list[int]:
    try:
        ids = [int(x) for x in sid.split("-") if x]
    except ValueError:
        raise HTTPException(400, "Búsqueda no válida")
    if not ids or len(ids) > 3:
        raise HTTPException(400, "Búsqueda no válida")
    return ids


@app.get(BASE + api + "/busquedas/{sid}")
async def resultados(sid: str, req: Request):
    usuario(req)
    with db() as c:
        pedidos = {row["hash"] for row in c.execute("SELECT hash FROM pedidos WHERE cancelado IS NULL")}
    filas: dict[str, dict] = {}
    estados, porcentajes = [], []
    for i in _ids(sid):
        r0 = await amule.pedir("GET", f"/search/{i}/results", params={"limit": 1000})
        if r0.status_code != 200:
            continue
        d = r0.json()
        p = d.get("progress", {})
        estados.append(p.get("state"))
        porcentajes.append(p.get("percent", 0))
        for r in d.get("results", []):
            nombres = [{"ecid": None, "nombre": r["name"], "fuentes": r["sources"]["total"]}]
            nombres += [{"ecid": a["ecid"], "nombre": a["name"], "fuentes": a["sources"]["total"]} for a in r.get("alternate_names", [])]
            # Nos quedamos con el nombre con extensión de libro más popular
            validos = [n for n in nombres if extension(n["nombre"]) in EXTENSIONES]
            if not validos:
                continue
            h = r["hash"].lower()
            if h in filas and filas[h]["fuentes"] >= r["sources"]["total"]:
                continue
            mejor = max(validos, key=lambda n: n["fuentes"])
            filas[h] = {
                "hash": h,
                "busqueda": i,
                "nombre": mejor["nombre"],
                "ecid": mejor["ecid"],
                "formato": extension(mejor["nombre"]),
                "tamano": r["size_bytes"],
                "fuentes": r["sources"]["total"],
                "completas": r["sources"]["complete"],
                "pedido": h in pedidos or r.get("already_downloaded", False),
            }
    if not estados:
        raise HTTPException(404, "La búsqueda ya no existe")
    lista = sorted(filas.values(), key=lambda f: (f["completas"], f["fuentes"]), reverse=True)
    return {
        "estado": "running" if "running" in estados else "finished",
        "porcentaje": min(porcentajes) if "running" in estados else 100,
        "resultados": lista[:200],
    }


@app.delete(BASE + api + "/busquedas/{sid}")
async def cerrar_busqueda(sid: str, req: Request):
    usuario(req)
    for i in _ids(sid):
        await amule.pedir("DELETE", f"/search/{i}")
    return {"ok": True}


class Pedido(BaseModel):
    hash: str
    nombre: str = Field(min_length=1, max_length=400)
    tamano: int = 0
    ecid: int | None = None


@app.post(BASE + api + "/pedidos")
async def pedir(p: Pedido, req: Request):
    u = usuario(req)
    if not HASH_RE.match(p.hash):
        raise HTTPException(400, "Hash no válido")
    if extension(p.nombre) not in EXTENSIONES:
        raise HTTPException(400, "Solo se pueden pedir libros electrónicos")
    h = p.hash.lower()
    with db() as c:
        activos = c.execute(
            "SELECT COUNT(*) FROM pedidos WHERE usuario=? AND cancelado IS NULL AND creado > ?",
            (u["usuario"], time.time() - 86400),
        ).fetchone()[0]
        if activos >= MAX_ACTIVOS_POR_USUARIO and not u["admin"]:
            raise HTTPException(429, f"Máximo {MAX_ACTIVOS_POR_USUARIO} pedidos al día")
    cuerpo = {"category_index": 0}
    if p.ecid is not None:
        cuerpo["ecid"] = p.ecid
    await amule.json("POST", f"/search/results/{h}/download", json=cuerpo)
    with db() as c:
        c.execute(
            "INSERT OR REPLACE INTO pedidos (hash,nombre,tamano,usuario,nombre_usuario,creado,cancelado) VALUES (?,?,?,?,?,?,NULL)",
            (h, p.nombre, p.tamano, u["usuario"], u["nombre"], time.time()),
        )
    # Para que el importador etiquete el libro con quien lo pidió
    (ESTADO / "pedidos" / f"{clave(p.nombre)}.json").write_text(
        json.dumps({"nombre": p.nombre, "usuario": u["usuario"], "hash": h}, ensure_ascii=False), "utf-8"
    )
    return {"ok": True}


@app.get(BASE + api + "/pedidos")
async def listar(req: Request, todos: bool = False):
    u = usuario(req)
    with db() as c:
        if todos:
            filas = c.execute("SELECT * FROM pedidos ORDER BY creado DESC LIMIT 200").fetchall()
        else:
            filas = c.execute("SELECT * FROM pedidos WHERE usuario=? ORDER BY creado DESC LIMIT 200", (u["usuario"],)).fetchall()
    try:
        d = await amule.json("GET", "/downloads", params={"status": "all", "limit": 1000000000})
        descargas = {x["hash"].lower(): x for x in d.get("downloads", [])}
        amule_ok = True
    except HTTPException:
        descargas, amule_ok = {}, False
    eventos = importados()
    salida = []
    for f in filas:
        item = {
            "hash": f["hash"], "nombre": f["nombre"], "tamano": f["tamano"], "usuario": f["usuario"],
            "nombre_usuario": f["nombre_usuario"], "creado": f["creado"],
            "propio": f["usuario"] == u["usuario"], "porcentaje": 0, "velocidad": 0, "fuentes": 0, "enlace": None,
        }
        ev = eventos.get(f["hash"]) or eventos.get(f["nombre"])
        dl = descargas.get(f["hash"])
        if f["cancelado"]:
            item["estado"] = "cancelado"
        elif ev and ev.get("estado") in ("importado", "duplicado"):
            item["estado"] = "en_biblioteca"
            item["porcentaje"] = 100
            if ev.get("ids"):
                item["enlace"] = enlace_libro(ev["ids"][0])
            item["duplicado"] = ev["estado"] == "duplicado"
        elif ev and ev.get("estado") == "error":
            item["estado"] = "error"
            item["detalle"] = "calibre no pudo importar el fichero"
        elif dl:
            item["porcentaje"] = round(dl.get("progress", {}).get("percent", 0), 1)
            item["velocidad"] = dl.get("speed_bytes_per_second", 0)
            item["fuentes"] = dl.get("sources", {}).get("total", 0)
            st = dl.get("status")
            item["estado"] = {
                "completed": "importando", "completing": "importando", "hashing": "importando",
                "paused": "pausado", "stopped": "pausado",
                "erroneous": "error", "insufficient_disk": "error",
            }.get(st, "descargando" if item["velocidad"] else "esperando")
        elif not amule_ok:
            item["estado"] = "desconocido"
        elif time.time() - f["creado"] < 120:
            item["estado"] = "esperando"
        else:
            # Ni en aMule ni importado: terminó y el importador aún no lo ha procesado,
            # o alguien lo quitó desde la interfaz de aMule.
            item["estado"] = "importando" if (INCOMING / f["nombre"]).exists() else "perdido"
        salida.append(item)
    return {"pedidos": salida, "amule": amule_ok}


@app.delete(BASE + api + "/pedidos/{h}")
async def cancelar(h: str, req: Request):
    u = usuario(req)
    if not HASH_RE.match(h):
        raise HTTPException(400, "Hash no válido")
    h = h.lower()
    with db() as c:
        f = c.execute("SELECT * FROM pedidos WHERE hash=?", (h,)).fetchone()
        if not f:
            raise HTTPException(404, "No existe ese pedido")
        if f["usuario"] != u["usuario"] and not u["admin"]:
            raise HTTPException(403, "Solo puedes cancelar tus pedidos")
        await amule.pedir("DELETE", f"/downloads/{h}")
        c.execute("UPDATE pedidos SET cancelado=? WHERE hash=?", (time.time(), h))
    return {"ok": True}


# ---------------------------------------------------------------- frontend
@app.get(BASE)
async def raiz_sin_barra():
    return RedirectResponse(BASE + "/", status_code=308)


@app.get(BASE + "/")
async def index():
    if INTERFAZ:
        return RedirectResponse(INTERFAZ, status_code=302)
    return FileResponse(STATIC / "index.html", headers={"Cache-Control": "no-cache"})


app.mount(BASE + "/static", StaticFiles(directory=STATIC), name="static")


@app.exception_handler(HTTPException)
async def errores(_: Request, e: HTTPException):
    return JSONResponse({"error": e.detail}, status_code=e.status_code)
