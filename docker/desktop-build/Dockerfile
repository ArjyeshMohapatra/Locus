FROM rust:1.93-bookworm

ARG DEBIAN_FRONTEND=noninteractive

RUN apt-get update && apt-get install -y --no-install-recommends \
    bash \
    build-essential \
    ca-certificates \
    clang \
    curl \
    git \
    llvm \
    mingw-w64 \
    patchelf \
    pkg-config \
    python3 \
    python3-dev \
    python3-pip \
    python3-venv \
    xdg-utils \
    libayatana-appindicator3-dev \
    libgtk-3-dev \
    libjavascriptcoregtk-4.1-dev \
    librsvg2-dev \
    libsoup-3.0-dev \
    libwebkit2gtk-4.1-dev \
    && rm -rf /var/lib/apt/lists/*

RUN curl -fsSL https://deb.nodesource.com/setup_20.x | bash - \
    && apt-get update && apt-get install -y --no-install-recommends nodejs \
    && rm -rf /var/lib/apt/lists/*

RUN rustup target add x86_64-unknown-linux-gnu x86_64-pc-windows-gnu x86_64-pc-windows-msvc
RUN cargo install tauri-cli --version 2.10.1 --locked

WORKDIR /workspace
