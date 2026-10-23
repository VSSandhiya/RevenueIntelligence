FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    REQUIRE_AUTH=true \
    DB_PATH=/app/state/app.db

WORKDIR /app
COPY requirements.txt ./requirements.txt
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
RUN mkdir -p /app/state /app/exports

EXPOSE 8000
CMD ["sh", "-c", "python -m uvicorn inbox.api:app --host 0.0.0.0 --port ${PORT:-8000}"]