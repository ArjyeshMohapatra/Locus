# LOCUS API Contract

Base URL: `http://localhost:8000`

## Overview
The frontend is built with **Tauri + Svelte**.
The backend is a **Python FastAPI** service.
Communication happens primarily via **HTTP Requests** from Svelte to Python.

## System Status
- `GET /health`
  - Returns component status (DB, Watcher, AI Engine)

## File Monitoring
- `GET /files/watched`
  - List all folders currently being monitored.
- `POST /files/watched`
  - Add a new folder to monitor.
  - Body: `{ "path": "C:\\Users\\..." }`
- `DELETE /files/watched/{id}`
  - Stop monitoring a path.

## File Recovery
- `GET /files/history`
  - Search version history.
  - Query params: `path`, `limit`, `date_from`, `date_to`.
- `POST /files/recover/{version_id}`
  - Restore a specific version of a file.
  - Body: `{ "target_path": "..." }` (Optional, defaults to original location)

## Activity Memory
- `GET /activity/timeline`
  - Get chronological list of events.
  - Query params: `start_time`, `end_time`, `event_types`.
- `GET /activity/search`
  - Semantic/Keyword search over activity logs and OCR text.
  - Query: `q` (e.g., "What was I working on yesterday?")

## Intelligence / NLU
- `POST /chat/query`
  - Natural language interface.
  - Body: `{ "query": "Find the PDF I edited last night" }`
  - Returns: `{ "response": "Found 'Report.pdf'...", "actions": [...] }`

## Snapshots (Recall)
- `GET /snapshots/recent`
  - Get recent screen context (blurred/metadata only).
- `DELETE /snapshots/{id}`
  - Manually delete a specific snapshot.

## UI Considerations (Tauri)
- **Window Management**: Handled by Tauri (Rust).
- **File Dialogs**: Handled by Tauri (Rust) for security/native feel, then paths sent to Python.
