FROM node:22-bookworm-slim AS frontend
WORKDIR /build
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY frontend/ ./
RUN npm run build

FROM python:3.13-slim-bookworm
WORKDIR /app
ENV PYTHONUNBUFFERED=1 PYTHONUTF8=1
RUN apt-get update \
    && apt-get install -y --no-install-recommends libgomp1 \
    && rm -rf /var/lib/apt/lists/*
COPY requirements-web.txt requirements-ml.txt requirements-db.txt ./
RUN pip install --no-cache-dir -r requirements-web.txt -r requirements-ml.txt -r requirements-db.txt
COPY backend/ ./backend/
COPY --from=frontend /build/dist ./frontend/dist/
CMD ["python", "-m", "uvicorn", "backend.main:app", "--host", "0.0.0.0", "--port", "8000"]
