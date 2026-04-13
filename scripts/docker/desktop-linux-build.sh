#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
IMAGE_NAME="locus-desktop-build-env:latest"

printf "[docker-linux] building image %s\n" "$IMAGE_NAME"
docker build -f "$ROOT_DIR/docker/desktop-build/Dockerfile" -t "$IMAGE_NAME" "$ROOT_DIR"

printf "[docker-linux] running linux desktop build in container\n"
docker run --rm -t \
  -v "$ROOT_DIR:/workspace" \
  -w /workspace \
  "$IMAGE_NAME" \
  bash -c '
    set -euo pipefail
    export PATH="/usr/local/cargo/bin:${PATH}"

    python3 -m venv /tmp/locus-build-venv
    source /tmp/locus-build-venv/bin/activate
    python -m pip install --upgrade pip
    pip install -r backend/requirements.txt
    pip install pyinstaller

    npm ci --prefix ui
    npm run build --prefix ui

    mkdir -p src-tauri/binaries

    python3 -m PyInstaller backend/app/main.py \
      --name locus-backend \
      --onefile \
      --paths backend \
      --distpath backend/dist \
      --workpath backend/build/pyinstaller \
      --specpath backend/build \
      --clean

    cp -f backend/dist/locus-backend src-tauri/binaries/locus-backend-x86_64-unknown-linux-gnu
    chmod +x src-tauri/binaries/locus-backend-x86_64-unknown-linux-gnu

    cargo build --release --manifest-path tools/window-probe/Cargo.toml --target x86_64-unknown-linux-gnu
    cp -f tools/window-probe/target/x86_64-unknown-linux-gnu/release/locus-window-probe src-tauri/binaries/locus-window-probe-x86_64-unknown-linux-gnu
    chmod +x src-tauri/binaries/locus-window-probe-x86_64-unknown-linux-gnu

    cd src-tauri
    cargo tauri build --config tauri.conf.json --target x86_64-unknown-linux-gnu
  '

printf "[docker-linux] done. Bundles in src-tauri/target/x86_64-unknown-linux-gnu/release/bundle\n"
