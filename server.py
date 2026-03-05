#!/usr/bin/env python3
import json
import sqlite3
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

BASE_DIR = Path(__file__).resolve().parent
DB_PATH = Path('/Users/openclaw/Desktop/March2026/databases/dev.db')
LOG_DB_PATH = Path('/Users/openclaw/Desktop/March2026/databases/log.db')

STATUS_ALIASES = {
    'todo': 'plan',
    'done': 'completed',
    'complete': 'completed',
    'working': 'in_progress',
}


def normalize_status(value: str, default: str = 'plan') -> str:
    raw = (value or default).strip().lower()
    return STATUS_ALIASES.get(raw, raw)


def get_conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute('PRAGMA foreign_keys = ON')
    return conn


def get_log_conn():
    conn = sqlite3.connect(LOG_DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def ensure_log_db():
    conn = get_log_conn()
    try:
        conn.execute('''
            CREATE TABLE IF NOT EXISTS work_logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ts TEXT NOT NULL DEFAULT (datetime('now')),
                agent TEXT NOT NULL,
                level TEXT NOT NULL DEFAULT 'info',
                message TEXT NOT NULL,
                meta_json TEXT
            )
        ''')
        conn.commit()
    finally:
        conn.close()


def add_log(agent: str, message: str, level: str = 'info', meta=None):
    conn = get_log_conn()
    try:
        conn.execute(
            'INSERT INTO work_logs (agent, level, message, meta_json) VALUES (?, ?, ?, ?)',
            (agent or 'system', level or 'info', message, json.dumps(meta) if meta is not None else None),
        )
        conn.commit()
    finally:
        conn.close()


def q(sql, params=()):
    conn = get_conn()
    try:
        cur = conn.execute(sql, params)
        return [dict(r) for r in cur.fetchall()]
    finally:
        conn.close()


class H(BaseHTTPRequestHandler):
    def _json(self, payload, code=200):
        b = json.dumps(payload, default=str).encode()
        self.send_response(code)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Content-Length', str(len(b)))
        self.send_header('Cache-Control', 'no-store')
        self.end_headers()
        self.wfile.write(b)

    def _read_json(self):
        ln = int(self.headers.get('Content-Length', '0') or '0')
        raw = self.rfile.read(ln) if ln else b'{}'
        return json.loads(raw.decode('utf-8') or '{}')

    def do_GET(self):
        u = urlparse(self.path)
        p = u.path
        if p == '/api/overview':
            total_tasks = q('select count(*) as c from tasks')[0]['c']
            completed = q("select count(*) as c from tasks where lower(status) in ('completed','done','complete')")[0]['c']
            pending_approval = q("select count(*) as c from tasks where lower(status)='approval_required'")[0]['c']
            complete_percentage = int(round((completed / total_tasks) * 100)) if total_tasks else 0
            return self._json({
                'total_tasks': total_tasks,
                'complete_percentage': complete_percentage,
                'pending_approval': pending_approval,
            })
        if p == '/api/categories':
            return self._json(q('select id,name,description,created_at,updated_at from categories order by id'))
        if p == '/api/projects':
            return self._json(q('''
                select p.id,p.name,p.status,p.priority,p.category_id,c.name as category_name,p.created_at,p.updated_at
                from projects p left join categories c on c.id=p.category_id order by p.id
            '''))
        if p == '/api/tasks':
            rows = q('''
                select t.id,t.title,t.description,t.status,t.assignee,t.due_at,t.project_id,p.name as project_name,t.created_at,t.updated_at
                from tasks t left join projects p on p.id=t.project_id order by t.id
            ''')
            for r in rows:
                r['status'] = normalize_status(r.get('status') or 'plan')
            return self._json(rows)
        if p == '/api/logs':
            qs = parse_qs(u.query or '')
            limit = int((qs.get('limit') or ['150'])[0])
            limit = max(1, min(1000, limit))
            conn = get_log_conn()
            try:
                cur = conn.execute(
                    'SELECT id, ts, agent, level, message, meta_json FROM work_logs ORDER BY id DESC LIMIT ?',
                    (limit,),
                )
                return self._json([dict(r) for r in cur.fetchall()])
            finally:
                conn.close()
        if p == '/' or p == '/index.html':
            html = (BASE_DIR / 'index.html').read_bytes()
            self.send_response(200)
            self.send_header('Content-Type', 'text/html; charset=utf-8')
            self.send_header('Content-Length', str(len(html)))
            self.end_headers()
            self.wfile.write(html)
            return
        self._json({'error': 'not found'}, 404)

    def do_POST(self):
        p = urlparse(self.path).path
        body = self._read_json()
        conn = get_conn()
        try:
            if p == '/api/logs/add':
                add_log(
                    agent=(body.get('agent') or 'system').strip().lower(),
                    message=(body.get('message') or '').strip(),
                    level=(body.get('level') or 'info').strip().lower(),
                    meta=body.get('meta'),
                )
                return self._json({'ok': True})

            if p == '/api/categories/add':
                name = (body.get('name') or '').strip()
                desc = (body.get('description') or '').strip() or None
                if not name:
                    return self._json({'error': 'name required'}, 400)
                conn.execute('insert into categories (name, description) values (?, ?)', (name, desc))
                conn.commit()
                add_log('system', f'Category added: {name}')
                return self._json({'ok': True})

            if p == '/api/categories/delete':
                ids = [int(x) for x in (body.get('ids') or [])]
                if not ids:
                    return self._json({'error': 'ids required'}, 400)
                conn.executemany('delete from categories where id=?', [(i,) for i in ids])
                conn.commit()
                add_log('system', f'Deleted {len(ids)} categorie(s)')
                return self._json({'ok': True, 'deleted': len(ids)})

            if p == '/api/projects/add':
                name = (body.get('name') or '').strip()
                if not name:
                    return self._json({'error': 'name required'}, 400)
                category_id = body.get('category_id')
                category_id = int(category_id) if category_id not in (None, '', 'null') else None
                status = normalize_status(body.get('status') or 'plan')
                priority = int(body.get('priority') or 3)
                conn.execute(
                    'insert into projects (name, category_id, status, priority) values (?, ?, ?, ?)',
                    (name, category_id, status, priority),
                )
                conn.commit()
                add_log('kai', f'Project added: {name}')
                return self._json({'ok': True})

            if p == '/api/projects/delete':
                ids = [int(x) for x in (body.get('ids') or [])]
                if not ids:
                    return self._json({'error': 'ids required'}, 400)
                conn.executemany('delete from projects where id=?', [(i,) for i in ids])
                conn.commit()
                add_log('kai', f'Deleted {len(ids)} project(s)')
                return self._json({'ok': True, 'deleted': len(ids)})

            if p == '/api/tasks/add':
                title = (body.get('title') or '').strip()
                project_id = body.get('project_id')
                if not title or project_id in (None, ''):
                    return self._json({'error': 'title and project_id required'}, 400)
                project_id = int(project_id)
                status = normalize_status(body.get('status') or 'plan')
                assignee = (body.get('assignee') or '').strip() or None
                description = (body.get('description') or '').strip() or None
                due_at = (body.get('due_at') or '').strip() or None
                conn.execute(
                    'insert into tasks (project_id, title, description, status, assignee, due_at) values (?, ?, ?, ?, ?, ?)',
                    (project_id, title, description, status, assignee, due_at),
                )
                conn.commit()
                add_log((assignee or 'david').lower(), f'Task added: {title}')
                return self._json({'ok': True})

            if p == '/api/tasks/delete':
                ids = [int(x) for x in (body.get('ids') or [])]
                if not ids:
                    return self._json({'error': 'ids required'}, 400)
                conn.executemany('delete from tasks where id=?', [(i,) for i in ids])
                conn.commit()
                add_log('david', f'Deleted {len(ids)} task(s)')
                return self._json({'ok': True, 'deleted': len(ids)})

            if p == '/api/tasks/update-status':
                task_id = body.get('task_id')
                status = normalize_status(body.get('status') or '')
                allowed = {'plan', 'in_progress', 'review', 'approval_required', 'completed', 'blocked'}
                if task_id in (None, '') or status not in allowed:
                    return self._json({'error': 'task_id and valid status required'}, 400)
                task_row = conn.execute('select id,title,status,assignee from tasks where id=?', (int(task_id),)).fetchone()
                if not task_row:
                    return self._json({'error': 'task not found'}, 404)
                conn.execute('update tasks set status=? where id=?', (status, int(task_id)))
                conn.commit()
                agent = (task_row['assignee'] or 'aran').strip().lower()
                add_log(agent, f"Task status changed: {task_row['title'] or ('#'+str(task_row['id']))} · {task_row['status'] or '-'} → {status}")
                return self._json({'ok': True})

            return self._json({'error': 'not found'}, 404)
        except sqlite3.IntegrityError as e:
            return self._json({'error': f'integrity error: {e}'}, 400)
        except Exception as e:
            return self._json({'error': str(e)}, 500)
        finally:
            conn.close()


if __name__ == '__main__':
    if not DB_PATH.exists():
        raise SystemExit(f'dev.db not found at {DB_PATH}')
    ensure_log_db()
    add_log('system', 'Control dashboard service started')
    srv = ThreadingHTTPServer(('127.0.0.1', 4174), H)
    print('control-dashboard running on http://127.0.0.1:4174')
    srv.serve_forever()
