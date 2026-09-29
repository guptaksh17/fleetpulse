FROM python:3.11-slim

WORKDIR /app

ENV PYTHONUNBUFFERED=1

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Installed as a package so runner_live and its relative imports resolve.
COPY . ./simulator

CMD ["python", "-m", "simulator.vehicle_simulator"]
