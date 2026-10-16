FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    APP_ENV=production \
    DATABASE_PATH=/data/oilcoin.db

WORKDIR /app
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt \
    && groupadd --gid 10001 oilcoin \
    && useradd --uid 10001 --gid oilcoin --no-create-home oilcoin \
    && mkdir /data \
    && chown oilcoin:oilcoin /data

COPY --chown=oilcoin:oilcoin . .
USER 10001:10001
EXPOSE 5000

HEALTHCHECK --interval=30s --timeout=10s --start-period=30s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:5000/health', timeout=8)" || exit 1

# One web process here; Compose runs worker.py separately on the same SQLite volume.
CMD ["gunicorn", "--no-control-socket", "--bind", "0.0.0.0:5000", "--workers", "1", "--worker-class", "gthread", "--threads", "4", "--timeout", "120", "--graceful-timeout", "120", "--worker-tmp-dir", "/tmp", "--access-logfile", "-", "--error-logfile", "-", "app:app"]
