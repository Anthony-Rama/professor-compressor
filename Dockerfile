FROM node:26-alpine@sha256:6bd9ab43c0ec0190ca7b2d193906cb8b7edf6e7f4afa43ddeccd6452710bffb5 AS web-assets

WORKDIR /build
COPY package.json package-lock.json ./
RUN npm ci --omit=dev


FROM python:3.11-slim@sha256:61e01f72680f86a5279ad510818ab7b2ee9f049822589d4e34e33e044360356c

ARG APP_REVISION=development
ENV APP_REVISION=${APP_REVISION}

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

COPY requirements.txt ./
RUN pip install --no-cache-dir --requirement requirements.txt \
    && useradd --create-home --uid 10001 compressor \
    && mkdir -p /data/observability \
    && chown compressor:compressor /data/observability \
    && chmod 700 /data/observability

COPY --from=web-assets /build/node_modules ./node_modules
COPY bot.py ./
COPY professor_compressor ./professor_compressor

USER compressor
EXPOSE 8080

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8080/readyz', timeout=3)"

CMD ["python", "bot.py"]
