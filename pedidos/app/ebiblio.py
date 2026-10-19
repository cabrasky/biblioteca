"""eBiblio (préstamo gratuito de libros electrónicos de las bibliotecas públicas).

Antes de descargar algo con aMule, se mira si el libro está en el catálogo de eBiblio de la
comunidad (EBIBLIO_URL, p. ej. https://madrid.ebiblio.es) para recomendar el préstamo.

La plataforma (Cantook / De Marque) no tiene API pública: se lee la página de resultados
/resources?q=..., que lleva microdatos schema.org (Book, Audiobook...). Cada resultado se puntúa
contra la búsqueda y solo se devuelven los que encajan de verdad (la búsqueda de eBiblio es muy
laxa: «sombra del viento» devuelve cualquier libro con «viento»).
"""
from __future__ import annotations

import html as htmlmod
import os
import re
import time
import unicodedata

import httpx

EBIBLIO_URL = os.environ.get("EBIBLIO_URL", "").rstrip("/")
EBIBLIO_NOMBRE = os.environ.get("EBIBLIO_NOMBRE", "eBiblio")
CACHE_SEG = int(os.environ.get("EBIBLIO_CACHE_SEG", "1800"))
UMBRAL = float(os.environ.get("EBIBLIO_UMBRAL", "0.75"))
TIPOS = {"Book": "Libro", "Audiobook": "Audiolibro"}

STOP = {
    "el", "la", "los", "las", "un", "una", "unos", "unas", "de", "del", "y", "e", "o", "u", "a", "al",
    "en", "por", "para", "con", "sin", "que", "the", "of", "and", "epub", "pdf", "mobi", "libro",
}

_cache: dict[str, tuple[float, list[dict]]] = {}
# Portadas devueltas por búsquedas recientes: solo esas se pueden pedir al proxy de portadas
_portadas: dict[str, float] = {}
_imgs: dict[str, tuple[float, bytes, str]] = {}
_cliente: httpx.AsyncClient | None = None


def activo() -> bool:
    return bool(EBIBLIO_URL)


def _norm(s: str) -> str:
    s = unicodedata.normalize("NFKD", s.lower())
    s = "".join(c for c in s if not unicodedata.combining(c))
    return re.sub(r"[^a-z0-9 ]+", " ", s)


def _palabras(s: str) -> list[str]:
    return [p for p in _norm(s).split() if p not in STOP and len(p) > 1]


def _puntuar(consulta: list[str], titulo: str, autores: list[str]) -> float:
    """Fracción de palabras de la búsqueda presentes en el título o el autor."""
    if not consulta:
        return 0.0
    vocab = set(_palabras(titulo)) | set(_palabras(" ".join(autores)))
    return round(sum(1 for p in consulta if p in vocab) / len(consulta), 3)


def _txt(s: str) -> str:
    return re.sub(r"\s+", " ", htmlmod.unescape(re.sub(r"<[^>]+>", " ", s))).strip()


def _parsear(pagina: str) -> list[dict]:
    items = []
    for tipo, art in re.findall(
        r'<article class="view-details" itemscope itemtype="http://schema.org/(\w+)">(.*?)</article>', pagina, re.S
    ):
        if tipo not in TIPOS:
            continue
        titulo = re.search(r'details-content__title[^>]*>(.*?)</h3>', art, re.S)
        enlace = re.search(r'href="(/resources/[0-9a-f]+)"', art)
        if not titulo or not enlace:
            continue
        portada = re.search(r'<img[^>]*class="cover"[^>]*src="([^"]+)"', art) or re.search(
            r'<img[^>]*src="([^"]+)"[^>]*class="cover"', art)
        disp = [_txt(x) for x in re.findall(r'<div class="availability">(.*?)</div>', art, re.S)]
        acciones = [_txt(x) for x in re.findall(r'<span class="action">(.*?)</span>', art)]
        formatos = [_txt(x) for x in re.findall(r'transaction__title[^>]*><span[^>]*>(.*?)</span>', art)]
        if "Prestar" in acciones:
            estado, texto = "disponible", (disp[0] if disp else "Disponible")
        elif "Reservar" in acciones:
            estado, texto = "reservable", "Prestado ahora: se puede reservar"
        else:
            estado, texto = "no_disponible", (disp[0] if disp else "No disponible ahora")
        items.append({
            "tipo": TIPOS[tipo],
            "titulo": _txt(titulo.group(1)),
            "autores": [_txt(x) for x in re.findall(r'itemprop="name">(.*?)</span>', art)][:3],
            "url": EBIBLIO_URL + enlace.group(1),
            "portada": htmlmod.unescape(portada.group(1)) if portada else None,
            "formatos": sorted(set(formatos)),
            "estado": estado,
            "disponibilidad": texto,
        })
    return items


async def buscar(q: str, maximo: int = 5) -> list[dict]:
    """Resultados de eBiblio que encajan con la búsqueda (mejor primero). Nunca lanza excepción:
    si eBiblio no responde, devuelve [] y se sigue con aMule."""
    global _cliente
    if not activo():
        return []
    clave = _norm(q).strip()
    ahora = time.time()
    if clave in _cache and ahora - _cache[clave][0] < CACHE_SEG:
        return _cache[clave][1][:maximo]
    if _cliente is None:
        _cliente = httpx.AsyncClient(timeout=12, follow_redirects=True,
                                     headers={"User-Agent": "biblioteca-cabrasky/1.0 (+https://libros.cabrasky.net)"})
    try:
        r = await _cliente.get(EBIBLIO_URL + "/resources", params={"q": q, "sort_by": "_score"})
        r.raise_for_status()
        items = _parsear(r.text)
    except (httpx.HTTPError, ValueError):
        return []
    consulta = _palabras(q)
    for it in items:
        it["puntuacion"] = _puntuar(consulta, it["titulo"], it["autores"])
    orden = {"disponible": 0, "reservable": 1, "no_disponible": 2}
    buenos = [it for it in items if it["puntuacion"] >= UMBRAL]
    buenos.sort(key=lambda it: (-it["puntuacion"], it["tipo"] != "Libro", orden[it["estado"]]))
    for it in buenos:
        if it["portada"]:
            _portadas[it["portada"]] = ahora
    _cache[clave] = (ahora, buenos)
    if len(_cache) > 500:
        for k in sorted(_cache, key=lambda k: _cache[k][0])[:250]:
            _cache.pop(k, None)
    return buenos[:maximo]


async def portada(url: str) -> tuple[bytes, str] | None:
    """Descarga (con caché) una portada que haya salido en una búsqueda. Calibre-Web solo permite
    imágenes del mismo dominio (CSP), así que el navegador las pide a través de este proxy."""
    if url not in _portadas or not url.startswith("https://") or _cliente is None:
        return None
    ahora = time.time()
    if url in _imgs and ahora - _imgs[url][0] < 86400:
        return _imgs[url][1], _imgs[url][2]
    try:
        r = await _cliente.get(url)
        r.raise_for_status()
    except httpx.HTTPError:
        return None
    tipo = r.headers.get("content-type", "")
    if not tipo.startswith("image/") or len(r.content) > 2_000_000:
        return None
    _imgs[url] = (ahora, r.content, tipo)
    if len(_imgs) > 300:
        for k in sorted(_imgs, key=lambda k: _imgs[k][0])[:150]:
            _imgs.pop(k, None)
    return r.content, tipo


async def cerrar() -> None:
    if _cliente is not None:
        await _cliente.aclose()
