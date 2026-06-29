FROM python:3.12-slim

WORKDIR /app
COPY backend /app
RUN pip install --no-cache-dir ".[optimization]" "psycopg[binary]"

ENV PYTHONPATH=/app
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
