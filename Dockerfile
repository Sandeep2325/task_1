FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 \
    SQLITE_PATH=/data/db.sqlite3 DEV_KEYS_DIR=/data/dev-keys

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .
RUN useradd --create-home app && mkdir -p /data && chown app /data
USER app

EXPOSE 8000
HEALTHCHECK CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/healthz')"
CMD ["sh", "-c", "python manage.py migrate --noinput && python manage.py seed_demo && uvicorn config.asgi:application --host 0.0.0.0 --port 8000"]
