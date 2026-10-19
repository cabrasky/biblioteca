#!/bin/sh
# [biblioteca] Modos de la imagen:
#   servidor   -> aplica la configuración gestionada (configurar.py) y arranca Calibre-Web
#   importador -> mete en la biblioteca lo que descarga aMule (calibredb directo sobre /biblioteca)
set -eu

case "${1:-servidor}" in
  servidor)
    if [ ! -f "${CW_BIBLIOTECA:-/biblioteca}/metadata.db" ]; then
      echo "Biblioteca vacía: inicializando ${CW_BIBLIOTECA:-/biblioteca}"
      calibredb --with-library "${CW_BIBLIOTECA:-/biblioteca}" list >/dev/null
    fi
    python3 /usr/local/bin/configurar.py
    exec python3 /app/cps.py -p /config/app.db -g /config/gdrive.db -i 0.0.0.0
    ;;
  importador)
    exec python3 /usr/local/bin/importador.py
    ;;
  *)
    exec "$@"
    ;;
esac
