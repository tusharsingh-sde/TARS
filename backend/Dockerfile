FROM python:3.12-slim

RUN useradd --uid 1000 --create-home --shell /bin/bash app

WORKDIR /app

COPY backend/pyproject.toml ./
RUN pip install --no-cache-dir .

COPY --chown=app:app data/ /app/data/
COPY --chown=app:app backend/ /app/
RUN pip install --no-cache-dir --no-deps . && chown -R app:app /app

USER app

ENV PORT=8000
EXPOSE 8000

CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000}"]

