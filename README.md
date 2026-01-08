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
