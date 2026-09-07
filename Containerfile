# Graea — run with podman (rootless-friendly), not docker.
#
#   podman build -t graea:latest -f Containerfile .
#   podman run --rm --userns=keep-id -v ./data:/data:Z --env-file .env graea:latest status --json
#
# --userns=keep-id matters: see the note above `USER graea` below for why.
#
# Bakes Chromium into the image (playwright install chromium) so a fresh
# container never needs network access on first run beyond the Telegram
# API itself. See AGENTS.md for the unattended install/login protocol.

FROM python:3.11-slim

# tesseract-ocr: OCR cross-check reader.
# The rest: Playwright Chromium's runtime shared-library dependencies
# (https://playwright.dev/python/docs/browsers#chromium) — Debian/Ubuntu
# names, matches python:3.11-slim's Debian base.
RUN apt-get update && apt-get install -y --no-install-recommends \
        tesseract-ocr \
        ca-certificates \
        fonts-liberation \
        libasound2 \
        libatk-bridge2.0-0 \
        libatk1.0-0 \
        libatspi2.0-0 \
        libcairo2 \
        libcups2 \
        libdbus-1-3 \
        libdrm2 \
        libgbm1 \
        libglib2.0-0 \
        libgtk-3-0 \
        libnspr4 \
        libnss3 \
        libpango-1.0-0 \
        libx11-6 \
        libxcb1 \
        libxcomposite1 \
        libxdamage1 \
        libxext6 \
        libxfixes3 \
        libxkbcommon0 \
        libxrandr2 \
        xdg-utils \
    && rm -rf /var/lib/apt/lists/*

ENV PLAYWRIGHT_BROWSERS_PATH=/ms-playwright \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

WORKDIR /app

COPY pyproject.toml README.md ./
COPY graea ./graea

# Install the package (+ demo extra: the buggy demo bot / python-telegram-bot)
# then bake Chromium into the image at the fixed browsers path above.
RUN pip install --no-cache-dir ".[demo]" \
    && playwright install chromium \
    && playwright install-deps chromium || true

# Non-root user, uid 1000 for convenience (a normal, low, human-looking
# uid). It does NOT make bind-mounted ./data writable by itself: under
# rootless podman, container uids map through /etc/subuid to a range of
# *host* uids (e.g. container uid 1000 -> host uid ~100999), so a host
# ./data owned by your actual host uid (1000) is not writable by this
# user unless you run with `--userns=keep-id` (see the top of this file
# and AGENTS.md) — that flag makes the container user's uid equal the
# host uid, so the bind mount just works. `chmod 0777 /data` below is a
# belt-and-braces fallback for the VOLUME default when --userns=keep-id
# isn't used.
RUN useradd --create-home --uid 1000 --shell /usr/sbin/nologin graea \
    && mkdir -p /data /ms-playwright \
    && chown -R graea:graea /app /data /ms-playwright \
    && chmod 0777 /data

VOLUME /data

# Under --userns=keep-id the process runs as the HOST uid, which may not be
# 1000 and has no home in the image; give Chromium/Playwright a writable HOME.
ENV HOME=/tmp \
    XDG_CACHE_HOME=/tmp/.cache \
    XDG_CONFIG_HOME=/tmp/.config

ENV GRAEA_SESSION=/data/graea.session \
    GRAEA_DB=/data/graea.duckdb \
    GRAEA_SHOTS=/data/shots \
    GRAEA_WEB_PROFILE=/data/web-profile \
    GRAEA_WEB_HEADLESS=true \
    GRAEA_IN_CONTAINER=1

USER graea

ENTRYPOINT ["graea"]
CMD ["status", "--json"]
