# Control Dashboard

Mission Control-style dashboard synced with SQLite `dev.db` in near real time.

## Features

- Overview cards:
  - Total Tasks
  - Complete Percentage
  - Pending Approval
- Kanban board (drag & drop)
  - Plan, In Progress, Review, Approval Required, Completed, Blocked
- User approval panel on the right with:
  - Accept / Reject
  - Optional note input
- Live system log feed with agent-specific colors/icons
- Categories / Projects / Tasks CRUD
  - Add single
  - Delete one or multiple via row selection
- Filters:
  - Projects: name + status
  - Tasks: title + status + assignee
- Light/Dark theme toggle (persisted in localStorage)
- Sidebar agent status (Aran, David, Kai)

## Data Source

`/Users/openclaw/Desktop/March2026/databases/dev.db`

## Run

```bash
cd /Users/openclaw/Desktop/March2026/control-dashboard
python3 server.py
```

Open:

- http://127.0.0.1:4174/

## API Endpoints

- `GET /api/overview`
- `GET /api/categories`
- `GET /api/projects`
- `GET /api/tasks`
- `POST /api/categories/add`
- `POST /api/categories/delete`
- `POST /api/projects/add`
- `POST /api/projects/delete`
- `POST /api/tasks/add`
- `POST /api/tasks/delete`
- `POST /api/tasks/update-status`

## Screenshot

![Control Dashboard](assets/dashboard-screenshot.jpg)
