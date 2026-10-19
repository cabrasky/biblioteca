#!/usr/bin/env python3
"""[biblioteca] Configuración gestionada de Calibre-Web (se aplica en cada arranque).

Calibre-Web guarda su configuración en /config/app.db (SQLite). Este script:
  1. Si la base aún no existe, arranca Calibre-Web un momento para que cree el esquema.
  2. Fija los ajustes que dependen del despliegue (no se tocan a mano en la web):
     biblioteca, español, título, login por cabecera del proxy (Authelia), binarios de calibre,
     subidas. El resto de ajustes se pueden cambiar libremente desde el panel de admin.
  3. La primera vez: contraseña aleatoria para el usuario local «admin» (admin123 por defecto)
     y español para los usuarios existentes.
Todo es configurable por entorno (CW_*); ver los valores por defecto abajo.
"""
import os
import secrets
import sqlite3
import subprocess
import sys
import time

APP_DB = os.environ.get("CW_APP_DB", "/config/app.db")
MARCA = os.path.join(os.path.dirname(APP_DB), ".inicializado")

ROLE_DOWNLOAD, ROLE_PASSWD, ROLE_EDIT_SHELFS, ROLE_VIEWER = 1 << 1, 1 << 4, 1 << 6, 1 << 8

GESTIONADOS = {
    "config_calibre_dir": os.environ.get("CW_BIBLIOTECA", "/biblioteca"),
    "config_calibre_web_title": os.environ.get("CW_TITULO", "Biblioteca"),
    "config_default_locale": os.environ.get("CW_IDIOMA", "es"),
    "config_binariesdir": "/opt/calibre",
    "config_converterpath": "/opt/calibre/ebook-convert",
    "config_uploading": 1,
    "config_anonbrowse": 0,
    "config_public_reg": 0,
    "config_allow_reverse_proxy_header_login": 1,
    "config_reverse_proxy_login_header_name": os.environ.get("CW_CABECERA_USUARIO", "Remote-User"),
    "config_reverse_proxy_trusted_ips": os.environ.get("CW_PROXIES_CONFIANZA", "10.42.0.0/16"),
    "config_logfile": "/dev/stdout",
    "config_access_log": 0,
}
# Solo la primera vez (luego se pueden cambiar desde el panel de admin)
INICIALES = {
    "config_default_language": "all",
    # Rol de los usuarios nuevos: descargar, leer en el navegador, estanterías y cambiar contraseña
    "config_default_role": ROLE_DOWNLOAD | ROLE_VIEWER | ROLE_EDIT_SHELFS | ROLE_PASSWD,
}


def log(msg):
    print("configurar: " + msg, flush=True)


def esquema_listo():
    if not os.path.exists(APP_DB):
        return False
    try:
        with sqlite3.connect(APP_DB, timeout=5) as c:
            return (c.execute("SELECT count(*) FROM settings").fetchone()[0] > 0
                    and c.execute("SELECT count(*) FROM user").fetchone()[0] > 0)
    except sqlite3.Error:
        return False


def crear_esquema():
    log("primera ejecución: creando la base de configuración de Calibre-Web")
    p = subprocess.Popen([sys.executable, "/app/cps.py", "-p", APP_DB, "-g",
                          os.path.join(os.path.dirname(APP_DB), "gdrive.db"), "-i", "127.0.0.1"],
                         stdout=subprocess.DEVNULL, stderr=subprocess.STDOUT)
    try:
        for _ in range(120):
            if esquema_listo():
                time.sleep(2)
                return
            if p.poll() is not None:
                raise SystemExit("Calibre-Web terminó antes de crear su base de datos")
            time.sleep(1)
        raise SystemExit("Calibre-Web no creó su base de datos a tiempo")
    finally:
        p.terminate()
        try:
            p.wait(20)
        except subprocess.TimeoutExpired:
            p.kill()


def aplicar(c, valores):
    columnas = {r[1] for r in c.execute("PRAGMA table_info(settings)")}
    for k, v in valores.items():
        if k in columnas:
            c.execute(f"UPDATE settings SET {k} = ?", (v,))
        else:
            log(f"aviso: la versión de Calibre-Web no tiene el ajuste {k}")


def main():
    if not esquema_listo():
        crear_esquema()
    from werkzeug.security import generate_password_hash
    with sqlite3.connect(APP_DB, timeout=30) as c:
        aplicar(c, GESTIONADOS)
        if not os.path.exists(MARCA):
            aplicar(c, INICIALES)
            c.execute("UPDATE user SET password = ? WHERE name = 'admin'",
                      (generate_password_hash(secrets.token_urlsafe(32)),))
            c.execute("UPDATE user SET locale = ? WHERE name != 'Guest'", (GESTIONADOS["config_default_locale"],))
            log("contraseña aleatoria para el usuario local «admin»; usuarios en español")
    if not os.path.exists(MARCA):
        open(MARCA, "w").close()
    log("configuración aplicada (biblioteca {}, idioma {}, proxy {} desde {})".format(
        GESTIONADOS["config_calibre_dir"], GESTIONADOS["config_default_locale"],
        GESTIONADOS["config_reverse_proxy_login_header_name"], GESTIONADOS["config_reverse_proxy_trusted_ips"]))


if __name__ == "__main__":
    main()
