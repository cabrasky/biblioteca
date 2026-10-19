# [biblioteca] Imagen de Calibre-Web (este fork) + binarios oficiales de calibre (conversión,
# metadatos y el importador de descargas de aMule). La construye Jenkins; la despliega Argo CD.
FROM debian:trixie-slim

ARG CALIBRE_VERSION=9.16.0

ENV DEBIAN_FRONTEND=noninteractive \
    LANG=C.UTF-8 \
    QT_QPA_PLATFORM=offscreen \
    CALIBRE_CONFIG_DIRECTORY=/tmp/calibre-config \
    CALIBRE_TEMP_DIR=/tmp \
    CALIBRE_DBPATH=/config \
    PATH=/opt/venv/bin:/opt/calibre:$PATH

# Bibliotecas que el binario oficial de calibre (Qt6) necesita aunque se use sin pantalla,
# más libmagic/ImageMagick para Calibre-Web (detección de tipos y portadas).
RUN apt-get update \
 && apt-get install -y --no-install-recommends \
      ca-certificates curl xz-utils tini python3 python3-venv \
      libmagic1t64 libmagickwand-7.q16-10 \
      libegl1 libopengl0 libgl1 libglx0 libxkbcommon0 libxkbcommon-x11-0 \
      libfontconfig1 libfreetype6 fonts-dejavu-core libnss3 libdbus-1-3 \
      libxcomposite1 libxdamage1 libxrandr2 libxtst6 libxkbfile1 libxcb-cursor0 \
      libxcb-icccm4 libxcb-image0 libxcb-keysyms1 libxcb-randr0 libxcb-render-util0 \
      libxcb-shape0 libxcb-xinerama0 libxcb-xkb1 libasound2t64 \
 && mkdir -p /opt/calibre \
 && curl -fsSL "https://download.calibre-ebook.com/${CALIBRE_VERSION}/calibre-${CALIBRE_VERSION}-x86_64.txz" \
      | tar -xJ -C /opt/calibre \
 && apt-get purge -y curl xz-utils && apt-get autoremove -y \
 && rm -rf /var/lib/apt/lists/* \
 && calibredb --version && rm -rf /tmp/calibre-config /root/.cache

# Calibre-Web desde el código de este repo (se ejecuta con cps.py, como en una instalación desde git)
COPY requirements.txt /app/requirements.txt
RUN python3 -m venv /opt/venv \
 && /opt/venv/bin/pip install --no-cache-dir -r /app/requirements.txt \
 && groupadd -g 1000 calibre && useradd -u 1000 -g 1000 -m -d /home/calibre calibre \
 && mkdir -p /biblioteca /config /descargas && chown calibre:calibre /biblioteca /config /descargas
COPY cps.py /app/cps.py
COPY cps /app/cps

COPY biblioteca/entrypoint.sh /usr/local/bin/entrypoint.sh
COPY biblioteca/configurar.py biblioteca/importador.py /usr/local/bin/
RUN chmod 755 /usr/local/bin/entrypoint.sh /usr/local/bin/configurar.py /usr/local/bin/importador.py

USER 1000:1000
EXPOSE 8083
ENTRYPOINT ["/usr/bin/tini", "--", "/usr/local/bin/entrypoint.sh"]
CMD ["servidor"]
