# syntax=docker/dockerfile:1

# ---------------------------------------------------------------- Frontend
FROM node:22-alpine AS frontend

WORKDIR /build
COPY frontend/package.json frontend/package-lock.json* ./
RUN npm ci 2>/dev/null || npm install
COPY frontend/ ./
RUN npm run build


# ----------------------------------------------------------------- Laufzeit
FROM python:3.13-slim AS runtime

# ffmpeg macht die Recodierung, ca-certificates braucht yt-dlp fuer TLS.
RUN apt-get update \
 && apt-get install -y --no-install-recommends \
      ffmpeg \
      ca-certificates \
      curl \
      unzip \
      tini \
      gosu \
 && rm -rf /var/lib/apt/lists/*

# ---------------------------------------------------------------------------
# JavaScript-Laufzeit - NICHT optional.
#
# Seit yt-dlp 2025.11.12 wird fuer volle YouTube-Unterstuetzung eine externe
# JS-Runtime gebraucht. Fehlt sie, bricht yt-dlp nicht ab, sondern liefert
# stillschweigend eine reduzierte Formatauswahl - im schlimmsten Fall 360p.
# Das faellt beim Testen mit einem einzelnen Video kaum auf und ruiniert
# unbemerkt ein ganzes Archiv. Deno ist die einzige Runtime, die yt-dlp ohne
# zusaetzliche Schalter selbst findet.
# ---------------------------------------------------------------------------
ARG DENO_VERSION=v2.9.6
RUN curl -fsSL "https://github.com/denoland/deno/releases/download/${DENO_VERSION}/deno-x86_64-unknown-linux-gnu.zip" -o /tmp/deno.zip \
 && unzip -q /tmp/deno.zip -d /usr/local/bin \
 && chmod +x /usr/local/bin/deno \
 && rm /tmp/deno.zip \
 && deno --version

WORKDIR /app

COPY backend/requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY backend/app ./app
COPY --from=frontend /build/dist ./static

# Der Nutzer "archiv" bekommt seine endgueltige ID erst beim Start: Der
# Entrypoint liest PUID/PGID und passt sie an, bevor er die Rechte abgibt.
# So gehoeren die Dateien auf einem NAS-Share dem richtigen Nutzer (Unraid:
# 99:100), ohne dass das Image dafuer neu gebaut werden muss.
RUN groupadd -g 1000 archiv \
 && useradd -u 1000 -g 1000 -m -s /bin/bash archiv \
 && mkdir -p /data && chown archiv:archiv /data /app
COPY docker/entrypoint.sh /app/entrypoint.sh
RUN sed -i 's/\r$//' /app/entrypoint.sh && chmod +x /app/entrypoint.sh

ENV YTA_DATA_DIR=/data \
    YTA_HOST=0.0.0.0 \
    YTA_PORT=8000 \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

VOLUME ["/data"]
EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/api/health', timeout=4).status==200 else 1)"

# tini als PID 1, damit abgebrochene ffmpeg-Kindprozesse ordentlich
# eingesammelt werden und keine Zombies zurueckbleiben.
ENTRYPOINT ["/usr/bin/tini", "--", "/app/entrypoint.sh"]
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
