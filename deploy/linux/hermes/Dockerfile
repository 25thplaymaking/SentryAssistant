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
# auto-upgraded in production. The pin is the DEFAULT here, not just a value in
# some deployment's .env -- previously this said `latest` and the only real pin
# lived in the server's untracked .env, so a clean checkout built whatever PyPI
# served that day while this comment claimed otherwise. `latest` still works if
# passed explicitly, for deliberately testing an upgrade.
ARG HERMES_VERSION=0.19.0
RUN pip install --no-cache-dir --user \
      $( [ "$HERMES_VERSION" = "latest" ] && echo "hermes-agent" || echo "hermes-agent==${HERMES_VERSION}" )

# The API server adapter is what the Sentry Gateway talks to, and Hermes only
# loads it when aiohttp is present. Without this the gateway starts but silently
# serves cron alone, so this is a hard requirement rather than an optional extra.
RUN pip install --no-cache-dir --user aiohttp

# MCP support is an OPTIONAL dependency of hermes-agent: without the `mcp` SDK,
# tools/mcp_tool.py sets _MCP_AVAILABLE=False and register_mcp_servers() returns
# an empty list, so every configured MCP server is silently ignored. Verified on
# 0.19.0: with the SDK installed the same config registered 14 tools.
#
# PINNED, and the pin is load-bearing (2026-08-18). This line used to be a bare
# `mcp`, and because it is an *optional* extra pip does not enforce the
# `mcp==1.26.0` that hermes-agent 0.19.0 declares -- so the Aug 15 build silently
# picked up mcp 2.0.0 and MCP broke in a way that looks like a config error:
#
#   - 2.0.0 removed the deprecated `streamablehttp_client` symbol. mcp_tool.py
#     derives its _MCP_HTTP_AVAILABLE flag from THAT name only, so HTTP
#     transport reported itself unavailable and every streamable-http server
#     failed with "Upgrade the mcp package to get HTTP support" -- while the
#     package was in fact NEWER than required.
#   - Forcing past that flag only reaches the next break: 2.0.0's
#     `streamable_http_client` yields 2 values where 0.19.0 unpacks 3
#     ("not enough values to unpack (expected 3, got 2)").
#
# Keep this pin matched to whatever hermes-agent's METADATA requires; check with
#   pip show -f hermes-agent | grep -i "requires-dist.*mcp"
# after any HERMES_VERSION bump.
RUN pip install --no-cache-dir --user 'mcp==1.26.0'

# Web search backend. The `web` toolset ships ENABLED but is gated shut by
# check_web_api_key(): with no backend credential and no importable provider it
# reports unavailable, and web_search/web_extract silently never register. The
# agent then has no internet access at all, which looks like a model limitation
# rather than a missing dependency.
#
# ddgs is the only supported backend whose availability is decided by a PACKAGE
# rather than an API key (_ddgs_package_importable), so it needs no account, no
# secret in .env, and no per-user signup. The keyed alternatives
# (TAVILY/EXA/BRAVE_SEARCH/FIRECRAWL/PARALLEL_API_KEY) and self-hosted
# SEARXNG_URL still work and take precedence when set.
#
# PINNED for the same reason `mcp` is: an unpinned optional dependency is how
# this image silently acquired an incompatible major version once already.
RUN pip install --no-cache-dir --user 'ddgs==9.6.0'

# Fix the api_server path so configured MCP servers reach the agent.
#
# Hermes calls discover_mcp_tools() before building the agent on the CLI, TUI and
# (since upstream #4219) cron paths — but NOT on the api_server path, which is
# the only one Sentry uses. Without this the tool registry is empty at turn time
# no matter what mcp_servers says. Verified: /v1/responses reported "NO MCP
# TOOLS" before, and exposes the mcp__* toolset after.
#
# The patch is idempotent and FAILS THE BUILD if the anchor moves, so a Hermes
# upgrade cannot silently drop it and leave MCP quietly broken again.
COPY --chown=hermes:hermes patches/api_server_mcp_discovery.py /tmp/
RUN python3 /tmp/api_server_mcp_discovery.py

COPY --chown=hermes:hermes server_control_mcp.py /opt/sentry-tools/server_control_mcp.py

EXPOSE 8642

# The API server binds loopback by default; inside a container it must bind the
# container interface so only the compose network can reach it. It is never
# published to a public interface.
CMD ["hermes", "gateway"]
