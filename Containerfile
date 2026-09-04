# Graea — run with podman (rootless-friendly), not docker.
#
#   podman build -t graea:latest -f Containerfile .
#   podman run --rm -v ./data:/data:Z --env-file .env graea:latest status --json
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

# Non-root user for rootless podman. uid 1000 matches the typical rootless
# subuid mapping so bind-mounted ./data stays writable from the host side.
RUN useradd --create-home --uid 1000 --shell /usr/sbin/nologin graea \
    && mkdir -p /data /ms-playwright \
    && chown -R graea:graea /app /data /ms-playwright

VOLUME /data

ENV GRAEA_SESSION=/data/graea.session \
    GRAEA_DB=/data/graea.duckdb \
    GRAEA_SHOTS=/data/shots \
    GRAEA_WEB_PROFILE=/data/web-profile \
    GRAEA_WEB_HEADLESS=true

USER graea

ENTRYPOINT ["graea"]
CMD ["status", "--json"]
