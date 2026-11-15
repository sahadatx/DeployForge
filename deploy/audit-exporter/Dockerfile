FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app
COPY backend/requirements.txt ./requirements.txt
RUN pip install --no-cache-dir -r requirements.txt boto3
COPY backend/app ./app
COPY scripts/export-audit-events.py ./scripts/export-audit-events.py

RUN useradd --create-home --uid 10001 exporter
USER 10001:10001

ENTRYPOINT ["python", "scripts/export-audit-events.py"]
