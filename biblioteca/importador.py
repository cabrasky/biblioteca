#!/usr/bin/env python3
"""Importador: mete en calibre los libros que aMule termina de descargar.

Vigila INCOMING (por defecto /descargas/incoming). Cada fichero con extensión de
libro electrónico, una vez estable, se añade a la biblioteca de calibre con
calibredb directamente sobre BIBLIOTECA (/biblioteca, la misma que sirve Calibre-Web).

Estado compartido con la app de pedidos en ESTADO (/descargas/.biblioteca):
  pedidos/<sha1(nombre)>.json   lo escribe la app de pedidos: {"nombre", "usuario", "hash"}
  importados.jsonl              lo escribe este proceso: un evento JSON por línea

Tras importar, el fichero se borra de incoming (calibre guarda su propia copia).
Si calibre lo rechaza, se mueve a /descargas/fallidos para revisarlo a mano.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

INCOMING = Path(os.environ.get("INCOMING", "/descargas/incoming"))
FALLIDOS = Path(os.environ.get("FALLIDOS", "/descargas/fallidos"))
ESTADO = Path(os.environ.get("ESTADO", "/descargas/.biblioteca"))
BIBLIOTECA = Path(os.environ.get("BIBLIOTECA", "/biblioteca"))
INTERVALO = int(os.environ.get("INTERVALO", "20"))
REPOSO = int(os.environ.get("REPOSO", "30"))  # segundos sin cambios antes de importar

EXTENSIONES = {
    "epub", "kepub", "mobi", "azw", "azw3", "azw4", "kfx", "pdf", "fb2", "fbz",
    "djvu", "cbz", "cbr", "cb7", "lit", "lrf", "pdb", "rtf", "docx", "odt", "txtz", "htmlz",
}

_vistos: dict[Path, tuple[int, float]] = {}


def log(msg: str) -> None:
    print(f"{datetime.now().strftime('%Y-%m-%d %H:%M:%S')} importador: {msg}", flush=True)


def clave(nombre: str) -> str:
    return hashlib.sha1(nombre.encode("utf-8")).hexdigest()


def pedido_de(nombre: str) -> dict:
    """Busca quién pidió el fichero. aMule puede añadir sufijos tipo '(1)' al nombre."""
    candidatos = [nombre, re.sub(r"\(\d+\)(\.[^.]+)$", r"\1", nombre)]
    for c in candidatos:
        f = ESTADO / "pedidos" / f"{clave(c)}.json"
        if f.exists():
            try:
                return json.loads(f.read_text("utf-8"))
            except (OSError, ValueError):
                pass
    return {}


def registrar(evento: dict) -> None:
    ESTADO.mkdir(parents=True, exist_ok=True)
    evento["fecha"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    with open(ESTADO / "importados.jsonl", "a", encoding="utf-8") as f:
        f.write(json.dumps(evento, ensure_ascii=False) + "\n")


def biblioteca_lista() -> bool:
    return (BIBLIOTECA / "metadata.db").is_file()


def estable(p: Path) -> bool:
    st = p.stat()
    firma = (st.st_size, st.st_mtime)
    previo = _vistos.get(p)
    _vistos[p] = firma
    return previo == firma and time.time() - st.st_mtime >= REPOSO and st.st_size > 0


def importar(p: Path) -> None:
    pedido = pedido_de(p.name)
    usuario = pedido.get("usuario")
    cmd = ["calibredb", "add", "--with-library", str(BIBLIOTECA)]
    if usuario:
        cmd += ["--tags", f"pedido:{usuario}"]
    cmd.append(str(p))
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
    salida = (r.stdout + r.stderr).strip()
    ids = [int(x) for x in re.findall(r"\d+", m.group(1))] if (m := re.search(r"Added book ids?:\s*([\d,\s]+)", salida)) else []
    base = {"nombre": p.name, "usuario": usuario, "hash": pedido.get("hash")}

    if r.returncode == 0 and ids:
        registrar({**base, "estado": "importado", "ids": ids})
        p.unlink(missing_ok=True)
        log(f"importado {p.name} -> ids {ids}" + (f" (pedido de {usuario})" if usuario else ""))
    elif r.returncode == 0 and "already exist" in salida:
        registrar({**base, "estado": "duplicado", "ids": []})
        p.unlink(missing_ok=True)
        log(f"ya estaba en la biblioteca: {p.name}")
    else:
        FALLIDOS.mkdir(parents=True, exist_ok=True)
        shutil.move(str(p), FALLIDOS / p.name)
        registrar({**base, "estado": "error", "ids": [], "detalle": salida[-500:]})
        log(f"ERROR importando {p.name} (movido a {FALLIDOS}): {salida[-300:]}")
    _vistos.pop(p, None)


def ciclo() -> None:
    if not INCOMING.is_dir():
        return
    for p in sorted(INCOMING.iterdir()):
        if not p.is_file() or p.name.startswith("."):
            continue
        if p.suffix.lower().lstrip(".") not in EXTENSIONES:
            continue
        try:
            if estable(p):
                importar(p)
        except FileNotFoundError:
            _vistos.pop(p, None)
        except Exception as e:  # un fichero problemático no debe tumbar el bucle
            log(f"fallo inesperado con {p.name}: {e!r}")
    for p in list(_vistos):
        if not p.exists():
            _vistos.pop(p, None)


def main() -> int:
    log(f"vigilando {INCOMING} cada {INTERVALO}s -> {BIBLIOTECA}")
    while True:
        if biblioteca_lista():
            ciclo()
        else:
            log(f"no hay biblioteca en {BIBLIOTECA}; reintento")
        time.sleep(INTERVALO)


if __name__ == "__main__":
    sys.exit(main())
