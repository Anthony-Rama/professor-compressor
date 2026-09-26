FROM node:22-alpine AS web-assets

WORKDIR /build
COPY package.json package-lock.json ./
RUN npm ci --omit=dev


FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

COPY requirements.txt ./
RUN pip install --no-cache-dir --requirement requirements.txt \
    && useradd --create-home --uid 10001 compressor

COPY --from=web-assets /build/node_modules ./node_modules
COPY bot.py app.py legal_pages.py media_validation.py metrics.py web_ui.py ./
COPY static ./static

USER compressor
EXPOSE 8080

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8080/healthz', timeout=3)"

CMD ["python", "bot.py"]
