# Code Changes Log

Generated: 2026-03-05 06:16:20

## Updated Files

### `server.py`
- Added persistent log database support (`log.db`).
- Added `work_logs` table bootstrap at service startup.
- Added API endpoints:
  - `GET /api/logs?limit=N`
  - `POST /api/logs/add`
- Integrated automatic logging for category/project/task mutations and task status transitions.

### `index.html`
- Reworked Activity Chat (System Logs) to load from persisted backend logs instead of in-memory only.
- Added `loadLogs()` flow and wired into refresh cycle.
- Updated `logEvent(...)` to persist events through `/api/logs/add`.

## Databases
- `dev.db` remains task/project/category source of truth.
- New `log.db` stores durable OpenClaw work/system logs in `work_logs`.
