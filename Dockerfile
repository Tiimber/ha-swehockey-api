# syntax=docker/dockerfile:1

# ── Build target: works on amd64 AND arm/v7 (Raspberry Pi 3) ──
FROM python:3.12-slim

LABEL org.opencontainers.image.title="HockeyLive API"
LABEL org.opencontainers.image.description="Swedish ice hockey schedule & live scores from swehockey.se"

# System deps for lxml
RUN apt-get update && apt-get install -y --no-install-recommends \
        libxml2 libxslt1.1 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY app.py scraper.py config.py ./

# config.yaml is provided at runtime via a bind-mount or volume:
#   docker run -v $(pwd)/config.yaml:/app/config.yaml ...
# For the Home Assistant add-on, it is placed in /data/config.yaml
#   (see homeassistant/addon/config.yaml for details).

EXPOSE 8080
ENV HOCKEY_CONFIG=/app/config.yaml

CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8080"]
