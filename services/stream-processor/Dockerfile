# Build context: repository root (see docker-compose.yml), so the shared feature
# definitions, configs and the DTC registry are copied from their single source.
FROM python:3.11-slim

WORKDIR /app

ENV PYTHONUNBUFFERED=1 \
    FEATURES_CONFIG=/app/configs/features.yaml \
    DTC_REGISTRY_PATH=/app/contracts/dtc/dtc_registry.yaml

COPY services/stream-processor/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY fleetpulse_features ./fleetpulse_features
COPY configs/features.yaml ./configs/features.yaml
COPY contracts/dtc/dtc_registry.yaml ./contracts/dtc/dtc_registry.yaml
COPY services/stream-processor/stream_processor ./stream_processor

CMD ["python", "-m", "stream_processor.main"]
