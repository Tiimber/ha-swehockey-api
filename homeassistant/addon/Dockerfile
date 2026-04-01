# syntax=docker/dockerfile:1
#
# Add-on Dockerfile for HockeyLive API.
#
# The HA Supervisor builds this image from the assembled add-on directory
# (created by scripts/build_addon.sh). All app source files must be present
# in the same directory as this Dockerfile when the image is built.

ARG BUILD_FROM=python:3.12-slim
FROM $BUILD_FROM

LABEL \
  io.hass.name="HockeyLive API" \
  io.hass.description="Swedish ice hockey live scores from swehockey.se" \
  io.hass.version="2.0.0" \
  io.hass.arch="aarch64|amd64|armv7"

# System deps required by lxml
RUN apt-get update && apt-get install -y --no-install-recommends \
        libxml2 libxslt1.1 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Python dependencies — copy first so this layer is cached independently
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

# All remaining files in the add-on directory (app.py, scraper.py, etc.)
COPY . ./
RUN chmod +x run.sh

# Data directory is mounted by the Supervisor at /data
# config.yaml and watchlist.json are generated/persisted there.
ENV HOCKEY_CONFIG=/data/config.yaml

EXPOSE 8080

CMD ["/app/run.sh"]
