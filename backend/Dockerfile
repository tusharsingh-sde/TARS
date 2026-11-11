FROM python:3.12-slim

RUN useradd --uid 1000 --create-home --shell /bin/bash app

WORKDIR /app

COPY pyproject.toml ./
RUN pip install --no-cache-dir .

COPY --chown=app:app . .
RUN pip install --no-cache-dir --no-deps . && chown -R app:app /app

USER app

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]

