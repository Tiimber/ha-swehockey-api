# syntax=docker/dockerfile:1
#
# Add-on Dockerfile for HockeyLive API.
#
# The HA Supervisor only includes config.yaml and this Dockerfile in the
# Docker build context — all other source code is downloaded from GitHub
# at build time. The repo must be public (or supply a token in REPO_URL).
# python:3.12-slim is a multi-arch image and pulls the correct variant (aarch64) automatically.

FROM python:3.12-slim

LABEL \
  io.hass.name="HockeyLive API" \
  io.hass.description="Swedish ice hockey live scores from swehockey.se" \
  io.hass.version="2.0.0" \
  io.hass.arch="aarch64|amd64|armv7"

# System deps: libxml2/libxslt for lxml, curl for source download
RUN apt-get update && apt-get install -y --no-install-recommends \
        libxml2 libxslt1.1 curl ca-certificates \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Download source from GitHub at build time.
# Bump io.hass.version above to bust the Docker layer cache on new releases.
ARG REPO_URL=https://github.com/Tiimber/ha-swehockey-api/archive/refs/heads/master.tar.gz
RUN curl -fsSL "${REPO_URL}" \
    | tar -xz --strip-components=1 \
    && pip3 install --no-cache-dir -r requirements.txt \
    && cp homeassistant/addon/run.sh homeassistant/addon/generate_config.py /app/ \
    && chmod +x /app/run.sh \
    && rm -rf homeassistant/ custom_components/ scripts/

# /data is mounted by the Supervisor — config.yaml and watchlist.json live there
ENV HOCKEY_CONFIG=/data/config.yaml

EXPOSE 8080

CMD ["/app/run.sh"]
