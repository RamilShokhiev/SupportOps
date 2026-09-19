FROM node:22-bookworm-slim AS frontend
WORKDIR /build/frontend
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

FROM python:3.13-slim-bookworm AS runtime
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_DISABLE_PIP_VERSION_CHECK=1
WORKDIR /app
COPY backend/requirements.txt backend/requirements.lock ./backend/
RUN pip install --no-cache-dir -r backend/requirements.txt && pip check
RUN useradd --create-home --uid 10001 supportops
COPY backend/ ./backend/
COPY retailbridge/ ./retailbridge/
COPY data/ ./data/
COPY alembic.ini ./
COPY --from=frontend /build/frontend/dist ./frontend/dist/
USER supportops
EXPOSE 8000
CMD ["python", "-m", "uvicorn", "backend.app.main:app", "--host", "0.0.0.0", "--port", "8000"]
