# Azizam — root image for the BBH channel bot + mini-app HTTP API.
# The deployment script expects this file at the repository root:
#   docker build -t azizam-bot:<sha> .
# The container serves BOTH the Telegram bot (polling) and the Mini App HTTP API.

FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    SHARED_DB=/data/bot_data.db \
    DB_PATH=/data/bot_data.db \
    HOST=0.0.0.0 \
    PORT=8080 \
    API_HOST=0.0.0.0 \
    API_PORT=8080

WORKDIR /app

COPY bot/requirements.txt /app/bot/requirements.txt
RUN pip install --no-cache-dir -r /app/bot/requirements.txt

COPY . /app

RUN mkdir -p /data && chmod 700 /data
VOLUME /data

# Container healthcheck (the deploy script refuses to launch without this).
HEALTHCHECK --interval=15s --timeout=6s --start-period=30s --retries=5 \
    CMD python -c "import urllib.request,sys;sys.exit(0) if urllib.request.urlopen('http://127.0.0.1:8080/health',timeout=4).status==200 else sys.exit(1)"

CMD ["python", "-u", "supervise.py"]
