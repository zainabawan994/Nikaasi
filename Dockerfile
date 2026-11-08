# Nikaasi — FastAPI backend + static frontend
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    APP_ENV=production \
    PORT=3000

WORKDIR /srv/nikaasi
# runtime dependencies only (test tools live in requirements-dev.txt)
COPY backend/requirements.txt backend/requirements.txt
RUN pip install --no-compile -r backend/requirements.txt

COPY backend backend
COPY public public

# run as an unprivileged user
RUN useradd --system --uid 10001 --home /srv/nikaasi nikaasi && chown -R nikaasi /srv/nikaasi
USER nikaasi

EXPOSE 3000
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:3000/api/health', timeout=4).status == 200 else 1)"

WORKDIR /srv/nikaasi/backend
# one worker: the simulation clock, SSE subscribers and rate limits live in-process
CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${PORT} --workers 1 --no-server-header --limit-concurrency ${MAX_CONNECTIONS:-300} --timeout-keep-alive 5 ${TRUST_PROXY:+--proxy-headers --forwarded-allow-ips=*}"]
