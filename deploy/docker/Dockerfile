FROM python:3.12-slim

WORKDIR /opt/project-alpha

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/opt/project-alpha:/opt/project-alpha/shared:/opt/project-alpha/services

RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    sqlite3 \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY services/ ./services/
COPY shared/ ./shared/

RUN mkdir -p /opt/project-alpha/scanner/data /opt/project-alpha/paper/data /opt/project-alpha/learner/data

CMD ["python", "-m", "services.scanner.main"]
