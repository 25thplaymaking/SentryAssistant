# Hermes Agent, pinned and run as a non-root user.
#
# One container per Sentry profile. Hermes selects its profile per process, so
# isolation comes from a dedicated HERMES_HOME and a dedicated container rather
# than from any in-process separation.
FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    DEBIAN_FRONTEND=noninteractive

# ripgrep, ffmpeg, git and node are the dependencies the Hermes installer expects.
RUN apt-get update && apt-get install -y --no-install-recommends \
        ca-certificates curl git ripgrep ffmpeg nodejs npm \
    && rm -rf /var/lib/apt/lists/*

# The runtime data directory is a bind mount owned by the host user, and chowning
# it would need root on the host. Instead the container user is created with the
# host owner's UID/GID so the mount is writable without any privileged step.
ARG HERMES_UID=1000
ARG HERMES_GID=1000
RUN groupadd --gid ${HERMES_GID} hermes 2>/dev/null || true \
    && useradd --uid ${HERMES_UID} --gid ${HERMES_GID} --create-home --shell /bin/bash hermes

USER hermes
WORKDIR /home/hermes

ENV HERMES_HOME=/home/hermes/.hermes \
    PATH=/home/hermes/.local/bin:$PATH

# Pin the release rather than tracking latest: the agent runtime is never
# auto-upgraded in production.
ARG HERMES_VERSION=latest
RUN pip install --no-cache-dir --user \
      $( [ "$HERMES_VERSION" = "latest" ] && echo "hermes-agent" || echo "hermes-agent==${HERMES_VERSION}" )

# The API server adapter is what the Sentry Gateway talks to, and Hermes only
# loads it when aiohttp is present. Without this the gateway starts but silently
# serves cron alone, so this is a hard requirement rather than an optional extra.
RUN pip install --no-cache-dir --user aiohttp

EXPOSE 8642

# The API server binds loopback by default; inside a container it must bind the
# container interface so only the compose network can reach it. It is never
# published to a public interface.
CMD ["hermes", "gateway"]
