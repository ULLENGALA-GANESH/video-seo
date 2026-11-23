FROM node:24-bookworm-slim AS frontend
WORKDIR /build/frontend
COPY frontend/package*.json ./
RUN npm ci --no-audit --no-fund
COPY frontend/ ./
RUN npm run build

FROM python:3.12-slim-bookworm
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 MEDIA_ROOT=/app/media FRONTEND_DIST=/app/frontend/dist
WORKDIR /app/backend
RUN apt-get update && apt-get install -y --no-install-recommends flac ffmpeg && rm -rf /var/lib/apt/lists/* \
    && useradd --create-home --uid 10001 studio && mkdir -p /app/media && chown studio:studio /app/media
ENV FFMPEG_BINARY=/usr/bin/ffmpeg
COPY backend/requirements.txt ./requirements.txt
RUN pip install --no-cache-dir -r requirements.txt
COPY backend/*.py ./
COPY --from=frontend /build/frontend/dist /app/frontend/dist
USER studio
EXPOSE 8000
CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8000"]
