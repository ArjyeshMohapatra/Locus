LOCUS — Local Context & Activity Memory

Quick start (development):

1. Backend (Python FastAPI)

```powershell
cd backend
python -m venv .venv
.\.venv\Scripts\activate
pip install -r requirements.txt
uvicorn app.main:app --reload
```

2. UI (Svelte + Vite)

```powershell
cd ui
npm install
npm run dev
```

3. Tauri (optional — dev tooling required)

Install Rust toolchain and `@tauri-apps/cli` to build native apps. For now the UI can run independently in dev mode and talk to `http://127.0.0.1:8000`.

Notes:
- The backend is intentionally decoupled from the UI. If you need to switch to Electron later, the Python API remains unchanged.
- `.gitignore` includes database and build artifacts.

Testing & QA Matrix:

1. Backend (pytest + black + ruff + mypy)

```bash
# from repo root
pip install -r backend/requirements-dev.txt

make backend-format
make backend-lint
make backend-type
make backend-test
```

2. Frontend (unit + Playwright e2e)

```bash
# install once
npm --prefix ui install
npm --prefix ui run playwright:install

# run checks
npm --prefix ui run test:unit
npm --prefix ui run test:e2e
```

3. Tauri (rust fmt + clippy + tests)

```bash
make tauri-fmt
make tauri-clippy
make tauri-test
```

4. Quick core sweep

```bash
make qa
```

Desktop Release Builds:

1. Linux (local)

```bash
# from repo root
./.venv/bin/python -m PyInstaller backend/app/main.py \
	--name locus-backend --onefile --paths backend \
	--distpath backend/dist --workpath backend/build/pyinstaller \
	--specpath backend/build --clean

cp -f backend/dist/locus-backend src-tauri/binaries/locus-backend-x86_64-unknown-linux-gnu
chmod +x src-tauri/binaries/locus-backend-x86_64-unknown-linux-gnu

env PKG_CONFIG_PATH="$PWD/src-tauri/pkgconfig-compat:$PKG_CONFIG_PATH" \
		LIBRARY_PATH="$PWD/src-tauri/lib-compat:$LIBRARY_PATH" \
		LD_LIBRARY_PATH="$PWD/src-tauri/lib-compat:$LD_LIBRARY_PATH" \
		RUSTFLAGS="-L native=$PWD/src-tauri/lib-compat $RUSTFLAGS" \
		cargo tauri build --target x86_64-unknown-linux-gnu
```

Artifacts are generated under:

- `src-tauri/target/x86_64-unknown-linux-gnu/release/bundle/appimage/`
- `src-tauri/target/x86_64-unknown-linux-gnu/release/bundle/deb/`
- `src-tauri/target/x86_64-unknown-linux-gnu/release/bundle/rpm/`

2. Windows + Linux (CI)

Use the GitHub Actions workflow `Desktop Release Builds` in `.github/workflows/desktop-build.yml`.

- Trigger manually with `workflow_dispatch`, or
- Push a tag like `v0.1.0`.

The workflow builds backend sidecars per OS, runs Tauri bundles for:

- `x86_64-unknown-linux-gnu`
- `x86_64-pc-windows-msvc`

and uploads artifacts for each platform.
