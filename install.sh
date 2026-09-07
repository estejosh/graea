#!/usr/bin/env bash
# Idempotent, non-interactive install for Graea.
#
#   ./install.sh                container mode (default): podman build + doctor
#   ./install.sh --no-container  venv mode: .venv + pip install -e + playwright
#
# Safe to re-run: build/install steps are no-ops (or fast) when already done,
# .env is created from .env.example only if missing (never overwritten).
set -eu

cd "$(dirname "$0")"

MODE="container"
if [ "${1:-}" = "--no-container" ]; then
    MODE="no-container"
fi

if [ ! -f .env ]; then
    cp .env.example .env
    echo "GRAEA_INSTALL: wrote .env from .env.example — fill in GRAEA_API_ID/GRAEA_API_HASH/GRAEA_PHONE/GRAEA_BOT (or GRAEA_SESSION_STRING) before logging in."
else
    echo "GRAEA_INSTALL: .env already exists, leaving it as-is."
fi

mkdir -p data

if [ "$MODE" = "no-container" ]; then
    echo "GRAEA_INSTALL: --no-container mode (python venv)."

    if ! command -v python3 >/dev/null 2>&1; then
        echo "GRAEA_INSTALL: error: python3 not found on PATH." >&2
        exit 1
    fi

    if [ ! -d .venv ]; then
        python3 -m venv .venv
    fi

    .venv/bin/pip install --upgrade pip >/dev/null
    .venv/bin/pip install -e ".[demo,dev]"
    .venv/bin/playwright install chromium

    echo "GRAEA_INSTALL: running doctor..."
    set +e
    .venv/bin/graea doctor
    DOCTOR_EXIT=$?
    set -e

    if [ "$DOCTOR_EXIT" -eq 0 ]; then
        echo "GRAEA_INSTALL: done. Next steps:"
        echo "GRAEA_INSTALL:   source .venv/bin/activate"
        echo "GRAEA_INSTALL:   graea login          # then graea login-web"
        echo "GRAEA_INSTALL:   graea status --json"
        echo "GRAEA_INSTALL:   graea mcp            # or: .venv/bin/graea mcp"
    else
        echo "GRAEA_INSTALL: doctor reported problems (see JSON above); fix .env and re-run: .venv/bin/graea doctor"
    fi

    exit "$DOCTOR_EXIT"
fi

# -- container mode ---------------------------------------------------------

if command -v podman >/dev/null 2>&1; then
    CONTAINER_CMD=podman
else
    echo "GRAEA_INSTALL: error: podman is required (this project targets podman, not docker/docker-compose)." >&2
    echo "GRAEA_INSTALL: install podman: https://podman.io/docs/installation" >&2
    exit 1
fi

echo "GRAEA_INSTALL: trying to pull ghcr.io/estejosh/graea:latest with $CONTAINER_CMD..."
if "$CONTAINER_CMD" pull ghcr.io/estejosh/graea:latest >/dev/null 2>&1; then
    "$CONTAINER_CMD" tag ghcr.io/estejosh/graea:latest graea:latest
    echo "GRAEA_INSTALL: image source: ghcr"
else
    echo "GRAEA_INSTALL: pull failed (private repo, no image published yet, or offline) — building locally."
    echo "GRAEA_INSTALL: building image (first run downloads ~400 MB, several minutes)…"
    "$CONTAINER_CMD" build -t graea:latest -f Containerfile .
    echo "GRAEA_INSTALL: image source: local"
fi

# Rootless podman maps container uid 1000 to a host subuid far from your
# actual host uid, so a freshly-created ./data (owned by your host uid)
# is NOT writable from inside the container unless the container is run
# with --userns=keep-id (see AGENTS.md / Containerfile). Verify that now,
# once, so `doctor` below doesn't fail on a permissions problem install.sh
# itself can fix.
if "$CONTAINER_CMD" run --rm --userns=keep-id -v "$(pwd)/data:/data:Z" --entrypoint sh \
        graea:latest -c 'touch /data/.w && rm /data/.w' >/dev/null 2>&1; then
    echo "GRAEA_INSTALL: data dir writable: yes"
else
    chmod -R a+rwX ./data
    if "$CONTAINER_CMD" run --rm --userns=keep-id -v "$(pwd)/data:/data:Z" --entrypoint sh \
            graea:latest -c 'touch /data/.w && rm /data/.w' >/dev/null 2>&1; then
        echo "GRAEA_INSTALL: data dir writable: repaired"
    else
        echo "GRAEA_INSTALL: data dir writable: NO"
    fi
fi

echo "GRAEA_INSTALL: running doctor inside the container..."
set +e
"$CONTAINER_CMD" run --rm --userns=keep-id -v "$(pwd)/data:/data:Z" --env-file .env graea:latest doctor
DOCTOR_EXIT=$?
set -e

if [ "$DOCTOR_EXIT" -eq 0 ]; then
    echo "GRAEA_INSTALL: done. Next steps:"
    echo "GRAEA_INSTALL:   $CONTAINER_CMD run -it --rm --userns=keep-id -v \$(pwd)/data:/data:Z --env-file .env graea:latest login"
    echo "GRAEA_INSTALL:   $CONTAINER_CMD run -it --rm --userns=keep-id -v \$(pwd)/data:/data:Z --env-file .env graea:latest login-web"
    echo "GRAEA_INSTALL:   $CONTAINER_CMD run --rm --userns=keep-id -v \$(pwd)/data:/data:Z --env-file .env graea:latest status --json"
    echo "GRAEA_INSTALL:   $CONTAINER_CMD run -i --rm --userns=keep-id -v \$(pwd)/data:/data:Z --env-file .env graea:latest mcp"
    echo "GRAEA_INSTALL: see AGENTS.md for the full unattended login protocol."
else
    echo "GRAEA_INSTALL: doctor reported problems (see JSON above); fix .env and re-run: $CONTAINER_CMD run --rm --userns=keep-id -v \$(pwd)/data:/data:Z --env-file .env graea:latest doctor"
fi

exit "$DOCTOR_EXIT"
