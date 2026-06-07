FROM node:22-bookworm-slim AS frontend

WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci

COPY index.html vite.config.ts vite.config.js tsconfig.json tsconfig.node.json ./
COPY src ./src
RUN npm run build

FROM python:3.12-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    APP_DB_PATH=/data/ai_committee.sqlite \
    REPORT_OUTPUT_DIR=/data/reports \
    AI_COMMITTEE_LIVE_QUOTES=true

RUN apt-get update \
    && apt-get install -y --no-install-recommends curl fonts-noto-cjk \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY backend/requirements.txt ./backend/requirements.txt
RUN pip install --no-cache-dir -r backend/requirements.txt

COPY backend ./backend
COPY scripts ./scripts
COPY --from=frontend /app/dist ./dist

RUN mkdir -p /data/reports /data/uploads

EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
  CMD curl -fsS http://127.0.0.1:${PORT:-8000}/api/health || exit 1

CMD ["sh", "-c", "uvicorn backend.app.main:app --host 0.0.0.0 --port ${PORT:-8000}"]
