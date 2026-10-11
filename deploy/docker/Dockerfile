FROM python:3.12-slim

WORKDIR /opt/project-beta
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 \
    PYTHONPATH=/opt/project-beta:/opt/project-beta/shared:/opt/project-beta/services

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY services/ ./services/
COPY shared/ ./shared/
COPY config/ ./config/
RUN mkdir -p scanner/data paper/data learner/data

CMD ["python", "-m", "services.scanner.main"]
