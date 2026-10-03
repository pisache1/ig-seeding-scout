FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1
WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app
COPY config.example.json mediakit.example.csv ./

# The SQLite file must live on a mounted volume; see SCOUT_DB_PATH below.
ENV SCOUT_DB_PATH=/data/scout.db
VOLUME /data

EXPOSE 8000
# $PORT is set by most hosts; 8000 is the fallback.
CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000}"]
