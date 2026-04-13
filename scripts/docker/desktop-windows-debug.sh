#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
IMAGE_NAME="locus-desktop-build-env:latest"
TARGET="${1:-x86_64-pc-windows-msvc}"

printf "[docker-win-debug] building image %s\n" "$IMAGE_NAME"
docker build -f "$ROOT_DIR/docker/desktop-build/Dockerfile" -t "$IMAGE_NAME" "$ROOT_DIR"

printf "[docker-win-debug] target=%s\n" "$TARGET"
if [[ "$TARGET" == "x86_64-pc-windows-msvc" ]]; then
  printf "[docker-win-debug] note: msvc linking is not available on linux containers; this run is for config/prebuild diagnostics.\n"
fi

docker run --rm -t \
  -v "$ROOT_DIR:/workspace" \
  -w /workspace \
  "$IMAGE_NAME" \
  bash -c "
    set -euo pipefail
    export PATH=\"/usr/local/cargo/bin:\${PATH}\"

    npm ci --prefix ui
    export RC_x86_64_pc_windows_msvc=llvm-rc

    mkdir -p src-tauri/binaries
    : > src-tauri/binaries/locus-backend-${TARGET}.exe
    : > src-tauri/binaries/locus-window-probe-${TARGET}.exe

    cd src-tauri

    set +e
    cargo tauri build --config tauri.conf.json --target ${TARGET} --no-bundle 2>&1 | tee /tmp/locus-windows-debug.log
    status=\${PIPESTATUS[0]}
    set -e

    echo "[docker-win-debug] cargo tauri exit code: \$status"
    if [ \$status -ne 0 ]; then
      echo "[docker-win-debug] tail of debug log:"
      tail -n 120 /tmp/locus-windows-debug.log || true
    fi
    exit \$status
  "
