# Build context: repository root. One image, two roles:
#   api     uvicorn app.main:app (REST API + dashboard)
#   scorer  python -m ml.score live (Redis snapshots -> calibrated risk -> Postgres, ML alerts)
FROM python:3.11-slim
WORKDIR /app
ENV PYTHONUNBUFFERED=1 PYTHONPATH=/app:/app/services/api
COPY services/api/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt && useradd --create-home --uid 10001 app
COPY ml ./ml
COPY services/api/app ./services/api/app
USER app
EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "2"]
