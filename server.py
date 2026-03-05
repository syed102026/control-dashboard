#!/usr/bin/env python3
import json
import shutil
import sqlite3
import time
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

BASE_DIR = Path(__file__).resolve().parent
DB_PATH = Path('/Users/openclaw/Desktop/March2026/databases/dev.db')
LOG_DB_PATH = Path('/Users/openclaw/Desktop/March2026/databases/log.db')
BACKUP_DIR = Path('/Users/openclaw/Desktop/March2026/databases/backups')

STATUS_ALIASES = {
    'todo': 'plan',
    'done': 'completed',
    'complete': 'completed',
    'working': 'in_progress',
}
ALLOWED_STATUS = {'plan', 'in_progress', 'review', 'approval_required', 'completed', 'blocked'}

EVENT_SEQ = 0


def bump_event_seq():
    global EVENT_SEQ
    EVENT_SEQ += 1


def normalize_status(value: str, default: str = 'plan') -> str:
    raw = (value or default).strip().lower()
    return STATUS_ALIASES.get(raw, raw)


def valid_status(value: str, default: str = 'plan') -> str:
    s = normalize_status(value, default)
    return s if s in ALLOWED_STATUS else default


def get_conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute('PRAGMA foreign_keys = ON')
    return conn


def get_log_conn():
    conn = sqlite3.connect(LOG_DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def ensure_dev_db():
    conn = get_conn()
    try:
        conn.execute('''
            CREATE TABLE IF NOT EXISTS deleted_tasks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                original_id INTEGER,
                project_id INTEGER,
                title TEXT,
                description TEXT,
                status TEXT,
                assignee TEXT,
                due_at TEXT,
                created_at TEXT,
                updated_at TEXT,
                deleted_at TEXT NOT NULL DEFAULT (datetime('now'))
            )
        ''')
        conn.execute('''
            CREATE TABLE IF NOT EXISTS deleted_projects (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                original_id INTEGER,
                category_id INTEGER,
                name TEXT,
                status TEXT,
                priority INTEGER,
                created_at TEXT,
                updated_at TEXT,
                description TEXT,
                deleted_at TEXT NOT NULL DEFAULT (datetime('now'))
            )
        ''')
        conn.execute('''
            CREATE TABLE IF NOT EXISTS handoffs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                task_id INTEGER,
                from_team TEXT NOT NULL,
                to_team TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'pending',
                note TEXT,
                created_at TEXT NOT NULL DEFAULT (datetime('now')),
                updated_at TEXT NOT NULL DEFAULT (datetime('now'))
            )
        ''')
        conn.execute('''
            CREATE TABLE IF NOT EXISTS inbox (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                agent TEXT NOT NULL,
                title TEXT NOT NULL,
                body TEXT,
                status TEXT NOT NULL DEFAULT 'unread',
                created_at TEXT NOT NULL DEFAULT (datetime('now')),
                updated_at TEXT NOT NULL DEFAULT (datetime('now'))
            )
        ''')
        conn.commit()
    finally:
        conn.close()


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
        conn.execute('''
            CREATE TABLE IF NOT EXISTS runtime_state (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL,
                updated_at TEXT NOT NULL DEFAULT (datetime('now'))
            )
        ''')
        conn.execute("INSERT OR IGNORE INTO runtime_state(key, value) VALUES('flow_state','stopped')")
        conn.commit()
    finally:
        conn.close()


def add_log(agent: str, message: str, level: str = 'info', meta=None):
    msg = (message or '').strip()
    if not msg:
        return
    conn = get_log_conn()
    try:
        row = conn.execute(
            '''
            SELECT id FROM work_logs
            WHERE agent=? AND level=? AND message=?
              AND ts >= datetime('now', '-2 seconds')
            ORDER BY id DESC LIMIT 1
            ''',
            ((agent or 'system').strip().lower(), (level or 'info').strip().lower(), msg),
        ).fetchone()
        if row:
            return

        conn.execute(
            'INSERT INTO work_logs (agent, level, message, meta_json) VALUES (?, ?, ?, ?)',
            ((agent or 'system').strip().lower(), (level or 'info').strip().lower(), msg, json.dumps(meta) if meta is not None else None),
        )
        conn.execute(
            '''
            DELETE FROM work_logs
            WHERE id NOT IN (
              SELECT id FROM work_logs ORDER BY id DESC LIMIT 2000
            )
            '''
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


def get_flow_state() -> str:
    conn = get_log_conn()
    try:
        row = conn.execute("SELECT value FROM runtime_state WHERE key='flow_state'").fetchone()
        return (row['value'] if row else 'stopped')
    finally:
        conn.close()


def set_flow_state(value: str):
    conn = get_log_conn()
    try:
        conn.execute(
            "UPDATE runtime_state SET value=?, updated_at=datetime('now') WHERE key='flow_state'",
            (value,),
        )
        conn.commit()
    finally:
        conn.close()


def create_backup():
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime('%Y%m%d-%H%M%S')
    dev_out = BACKUP_DIR / f'dev-{ts}.db'
    log_out = BACKUP_DIR / f'log-{ts}.db'
    shutil.copy2(DB_PATH, dev_out)
    if LOG_DB_PATH.exists():
        shutil.copy2(LOG_DB_PATH, log_out)
    return {'timestamp': ts, 'dev': str(dev_out), 'log': str(log_out)}


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
        if p == '/api/events':
            self.send_response(200)
            self.send_header('Content-Type', 'text/event-stream')
            self.send_header('Cache-Control', 'no-cache')
            self.send_header('Connection', 'keep-alive')
            self.end_headers()
            last = -1
            try:
                while True:
                    if EVENT_SEQ != last:
                        last = EVENT_SEQ
                        self.wfile.write(f"data: {json.dumps({'seq': last, 'ts': datetime.now().isoformat()})}\n\n".encode())
                    else:
                        self.wfile.write(b': ping\n\n')
                    self.wfile.flush()
                    time.sleep(2)
            except Exception:
                return

        if p == '/api/flow/status':
            return self._json({'state': get_flow_state()})

        if p == '/api/agents-summary':
            agents = ['Atlas','Aran','David','Quinn','Kai','Jerry','Nikola','Albert','Warren','Steve','Philip']
            rows = []
            log_conn = get_log_conn()
            try:
                for a in agents:
                    st = q("""
                        SELECT
                          SUM(CASE WHEN lower(status)='in_progress' THEN 1 ELSE 0 END) as in_progress,
                          SUM(CASE WHEN lower(status)='review' THEN 1 ELSE 0 END) as review,
                          SUM(CASE WHEN lower(status)='blocked' THEN 1 ELSE 0 END) as blocked,
                          SUM(CASE WHEN lower(status)='completed' THEN 1 ELSE 0 END) as completed
                        FROM tasks WHERE lower(coalesce(assignee,''))=lower(?)
                    """, (a,))[0]
                    cur = log_conn.execute("SELECT ts, message FROM work_logs WHERE lower(agent)=lower(?) ORDER BY id DESC LIMIT 1", (a,))
                    last = cur.fetchone()
                    rows.append({
                        'agent': a,
                        'in_progress': int(st.get('in_progress') or 0),
                        'review': int(st.get('review') or 0),
                        'blocked': int(st.get('blocked') or 0),
                        'completed': int(st.get('completed') or 0),
                        'last_activity_at': last['ts'] if last else None,
                        'last_activity': last['message'] if last else None,
                    })
            finally:
                log_conn.close()
            return self._json(rows)

        if p == '/api/ops-metrics':
            total = q('SELECT COUNT(*) c FROM tasks')[0]['c']
            completed = q("SELECT COUNT(*) c FROM tasks WHERE lower(status)='completed'")[0]['c']
            blocked = q("SELECT COUNT(*) c FROM tasks WHERE lower(status)='blocked'")[0]['c']
            review = q("SELECT COUNT(*) c FROM tasks WHERE lower(status)='review'")[0]['c']
            in_progress = q("SELECT COUNT(*) c FROM tasks WHERE lower(status)='in_progress'")[0]['c']
            stale = q("SELECT COUNT(*) c FROM tasks WHERE lower(status)='in_progress' AND updated_at < datetime('now','-1 day')")[0]['c']
            handoff_pending = q("SELECT COUNT(*) c FROM handoffs WHERE lower(status)='pending'")[0]['c']
            return self._json({
                'total_tasks': total,
                'completed_tasks': completed,
                'completion_rate': int(round((completed / total) * 100)) if total else 0,
                'blocked_tasks': blocked,
                'review_tasks': review,
                'in_progress_tasks': in_progress,
                'stale_in_progress': stale,
                'pending_handoffs': handoff_pending,
            })

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
            qs = parse_qs(u.query or '')
            limit = max(1, min(1000, int((qs.get('limit') or ['1000'])[0])))
            offset = max(0, int((qs.get('offset') or ['0'])[0]))
            rows = q('''
                select p.id,p.name,p.status,p.priority,p.category_id,c.name as category_name,p.created_at,p.updated_at
                from projects p left join categories c on c.id=p.category_id
                order by p.id limit ? offset ?
            ''', (limit, offset))
            for r in rows:
                r['status'] = valid_status(r.get('status') or 'plan')
            return self._json(rows)
        if p == '/api/tasks':
            qs = parse_qs(u.query or '')
            limit = max(1, min(5000, int((qs.get('limit') or ['2000'])[0])))
            offset = max(0, int((qs.get('offset') or ['0'])[0]))
            rows = q('''
                select t.id,t.title,t.description,t.status,t.assignee,t.team,t.phase,t.acceptance_criteria,t.evidence_link,t.due_at,t.project_id,p.name as project_name,t.created_at,t.updated_at
                from tasks t left join projects p on p.id=t.project_id
                order by t.id limit ? offset ?
            ''', (limit, offset))
            for r in rows:
                r['status'] = valid_status(r.get('status') or 'plan')
            return self._json(rows)
        if p == '/api/trash':
            return self._json({
                'deleted_tasks': q('select id,original_id,title,project_id,deleted_at from deleted_tasks order by id desc limit 20'),
                'deleted_projects': q('select id,original_id,name,deleted_at from deleted_projects order by id desc limit 20'),
            })
        if p == '/api/deleted-tasks':
            qs = parse_qs(u.query or '')
            limit = max(1, min(5000, int((qs.get('limit') or ['2000'])[0])))
            offset = max(0, int((qs.get('offset') or ['0'])[0]))
            rows = q('''
                select dt.id, dt.original_id, dt.project_id, p.name as project_name, dt.title, dt.status, dt.assignee, dt.deleted_at
                from deleted_tasks dt
                left join projects p on p.id = dt.project_id
                order by dt.id desc limit ? offset ?
            ''', (limit, offset))
            for r in rows:
                r['status'] = valid_status(r.get('status') or 'plan')
            return self._json(rows)
        if p == '/api/handoffs':
            return self._json(q('''
                select h.id,h.task_id,t.title as task_title,h.from_team,h.to_team,h.status,h.note,h.created_at,h.updated_at
                from handoffs h left join tasks t on t.id=h.task_id
                order by h.id desc
            '''))
        if p == '/api/inbox':
            qs = parse_qs(u.query or '')
            agent = (qs.get('agent') or [''])[0]
            if agent:
                return self._json(q("select * from inbox where lower(agent)=lower(?) order by id desc", (agent,)))
            return self._json(q("select * from inbox order by id desc"))
        if p == '/api/logs':
            qs = parse_qs(u.query or '')
            limit = int((qs.get('limit') or ['150'])[0])
            offset = int((qs.get('offset') or ['0'])[0])
            limit = max(1, min(1000, limit))
            offset = max(0, offset)
            conn = get_log_conn()
            try:
                cur = conn.execute(
                    'SELECT id, ts, agent, level, message, meta_json FROM work_logs ORDER BY id DESC LIMIT ? OFFSET ?',
                    (limit, offset),
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
                bump_event_seq()
                return self._json({'ok': True})

            if p == '/api/flow/start':
                set_flow_state('running')
                has_running = conn.execute("SELECT id FROM tasks WHERE lower(status)='in_progress' LIMIT 1").fetchone()
                promoted_task = None
                if not has_running:
                    next_task = conn.execute("SELECT id,title,assignee FROM tasks WHERE lower(status)='plan' ORDER BY id LIMIT 1").fetchone()
                    if next_task:
                        conn.execute("UPDATE tasks SET status='in_progress' WHERE id=?", (next_task['id'],))
                        promoted_task = {'id': next_task['id'], 'title': next_task['title']}
                conn.commit()
                note = ''
                if promoted_task:
                    note = f" · Task #{promoted_task['id']} moved to in_progress"
                add_log('system', f"Flow started{note}")
                bump_event_seq()
                return self._json({'ok': True, 'state': 'running', 'promoted_task': promoted_task})

            if p == '/api/flow/pause':
                set_flow_state('paused')
                add_log('system', 'Flow paused')
                bump_event_seq()
                return self._json({'ok': True, 'state': 'paused'})

            if p == '/api/flow/stop':
                set_flow_state('stopped')
                add_log('system', 'Flow stopped')
                bump_event_seq()
                return self._json({'ok': True, 'state': 'stopped'})

            if p == '/api/handoffs/add':
                task_id = body.get('task_id')
                from_team = (body.get('from_team') or '').strip().lower()
                to_team = (body.get('to_team') or '').strip().lower()
                note = (body.get('note') or '').strip() or None
                if not from_team or not to_team:
                    return self._json({'error': 'from_team and to_team required'}, 400)
                conn.execute('insert into handoffs(task_id,from_team,to_team,status,note) values(?,?,?,?,?)',
                             (int(task_id) if task_id else None, from_team, to_team, 'pending', note))
                conn.commit()
                add_log('system', f'Handoff created: {from_team} -> {to_team}')
                bump_event_seq()
                return self._json({'ok': True})

            if p == '/api/handoffs/update':
                hid = body.get('handoff_id')
                status = (body.get('status') or '').strip().lower()
                note = (body.get('note') or '').strip() or None
                if hid in (None, '') or status not in {'pending','accepted','rejected','completed'}:
                    return self._json({'error': 'handoff_id and valid status required'}, 400)
                conn.execute('update handoffs set status=?, note=coalesce(?,note), updated_at=datetime(\'now\') where id=?', (status, note, int(hid)))
                conn.commit()
                add_log('system', f'Handoff #{hid} moved to {status}')
                bump_event_seq()
                return self._json({'ok': True})

            if p == '/api/inbox/add':
                agent = (body.get('agent') or '').strip()
                title = (body.get('title') or '').strip()
                msg = (body.get('body') or '').strip() or None
                if not agent or not title:
                    return self._json({'error': 'agent and title required'}, 400)
                conn.execute('insert into inbox(agent,title,body,status) values(?,?,?,?)', (agent, title, msg, 'unread'))
                conn.commit()
                add_log('system', f'Inbox item added for {agent}: {title}')
                bump_event_seq()
                return self._json({'ok': True})

            if p == '/api/inbox/update':
                iid = body.get('inbox_id')
                status = (body.get('status') or '').strip().lower()
                if iid in (None, '') or status not in {'unread','read','done'}:
                    return self._json({'error': 'inbox_id and valid status required'}, 400)
                conn.execute('update inbox set status=?, updated_at=datetime(\'now\') where id=?', (status, int(iid)))
                conn.commit()
                bump_event_seq()
                return self._json({'ok': True})

            if p == '/api/backup/create':
                out = create_backup()
                add_log('system', f"Backup created: {Path(out['dev']).name}")
                bump_event_seq()
                return self._json({'ok': True, 'backup': out})

            if p == '/api/categories/add':
                name = (body.get('name') or '').strip()
                desc = (body.get('description') or '').strip() or None
                if not name:
                    return self._json({'error': 'name required'}, 400)
                conn.execute('insert into categories (name, description) values (?, ?)', (name, desc))
                conn.commit()
                add_log('system', f'Category added: {name}')
                bump_event_seq()
                return self._json({'ok': True})

            if p == '/api/categories/delete':
                ids = [int(x) for x in (body.get('ids') or [])]
                if not ids:
                    return self._json({'error': 'ids required'}, 400)
                conn.executemany('delete from categories where id=?', [(i,) for i in ids])
                conn.commit()
                add_log('system', f'Deleted {len(ids)} categorie(s)')
                bump_event_seq()
                return self._json({'ok': True, 'deleted': len(ids)})

            if p == '/api/projects/add':
                name = (body.get('name') or '').strip()
                if not name:
                    return self._json({'error': 'name required'}, 400)
                category_id = body.get('category_id')
                category_id = int(category_id) if category_id not in (None, '', 'null') else None
                status = valid_status(body.get('status') or 'plan')
                priority = int(body.get('priority') or 3)
                conn.execute(
                    'insert into projects (name, category_id, status, priority) values (?, ?, ?, ?)',
                    (name, category_id, status, priority),
                )
                conn.commit()
                add_log('kai', f'Project added: {name}')
                bump_event_seq()
                return self._json({'ok': True})

            if p == '/api/projects/delete':
                ids = [int(x) for x in (body.get('ids') or [])]
                if not ids:
                    return self._json({'error': 'ids required'}, 400)
                for i in ids:
                    row = conn.execute('select * from projects where id=?', (i,)).fetchone()
                    if row:
                        conn.execute('''
                            insert into deleted_projects(original_id, category_id, name, status, priority, created_at, updated_at, description)
                            values(?,?,?,?,?,?,?,?)
                        ''', (row['id'], row['category_id'], row['name'], row['status'], row['priority'], row['created_at'], row['updated_at'], row['description']))
                conn.executemany('delete from projects where id=?', [(i,) for i in ids])
                conn.commit()
                add_log('kai', f'Deleted {len(ids)} project(s)')
                bump_event_seq()
                return self._json({'ok': True, 'deleted': len(ids)})

            if p == '/api/projects/restore-latest':
                row = conn.execute('select * from deleted_projects order by id desc limit 1').fetchone()
                if not row:
                    return self._json({'error': 'no deleted project to restore'}, 404)
                conn.execute('''
                    insert into projects (category_id, name, status, priority, created_at, updated_at, description)
                    values (?, ?, ?, ?, datetime('now'), datetime('now'), ?)
                ''', (row['category_id'], row['name'], valid_status(row['status']), row['priority'] or 3, row['description']))
                conn.execute('delete from deleted_projects where id=?', (row['id'],))
                conn.commit()
                add_log('kai', f"Project restored: {row['name']}")
                bump_event_seq()
                return self._json({'ok': True})

            if p == '/api/tasks/add':
                title = (body.get('title') or '').strip()
                project_id = body.get('project_id')
                if not title or project_id in (None, ''):
                    return self._json({'error': 'title and project_id required'}, 400)
                project_id = int(project_id)
                status = valid_status(body.get('status') or 'plan')
                assignee = (body.get('assignee') or '').strip() or None
                description = (body.get('description') or '').strip() or None
                team = (body.get('team') or 'saas').strip().lower()
                phase = (body.get('phase') or 'development').strip().lower()
                acceptance_criteria = (body.get('acceptance_criteria') or '').strip() or None
                evidence_link = (body.get('evidence_link') or '').strip() or None
                due_at = (body.get('due_at') or '').strip() or None
                conn.execute(
                    'insert into tasks (project_id, title, description, status, assignee, team, phase, acceptance_criteria, evidence_link, due_at) values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)',
                    (project_id, title, description, status, assignee, team, phase, acceptance_criteria, evidence_link, due_at),
                )
                conn.commit()
                add_log((assignee or 'david').lower(), f'Task added: {title}')
                bump_event_seq()
                return self._json({'ok': True})

            if p == '/api/tasks/delete':
                ids = [int(x) for x in (body.get('ids') or [])]
                if not ids:
                    return self._json({'error': 'ids required'}, 400)
                for i in ids:
                    row = conn.execute('select * from tasks where id=?', (i,)).fetchone()
                    if row:
                        conn.execute('''
                            insert into deleted_tasks(original_id, project_id, title, description, status, assignee, due_at, created_at, updated_at)
                            values(?,?,?,?,?,?,?,?,?)
                        ''', (row['id'], row['project_id'], row['title'], row['description'], row['status'], row['assignee'], row['due_at'], row['created_at'], row['updated_at']))
                conn.executemany('delete from tasks where id=?', [(i,) for i in ids])
                conn.commit()
                add_log('david', f'Deleted {len(ids)} task(s)')
                bump_event_seq()
                return self._json({'ok': True, 'deleted': len(ids)})

            if p == '/api/tasks/restore-latest':
                row = conn.execute('select * from deleted_tasks order by id desc limit 1').fetchone()
                if not row:
                    return self._json({'error': 'no deleted task to restore'}, 404)
                conn.execute('''
                    insert into tasks (project_id, title, description, status, assignee, due_at, created_at, updated_at)
                    values (?, ?, ?, ?, ?, ?, datetime('now'), datetime('now'))
                ''', (row['project_id'], row['title'], row['description'], valid_status(row['status']), row['assignee'], row['due_at']))
                conn.execute('delete from deleted_tasks where id=?', (row['id'],))
                conn.commit()
                add_log('david', f"Task restored: {row['title']}")
                bump_event_seq()
                return self._json({'ok': True})

            if p == '/api/deleted-tasks/purge':
                ids = [int(x) for x in (body.get('ids') or [])]
                if not ids:
                    return self._json({'error': 'ids required'}, 400)
                conn.executemany('delete from deleted_tasks where id=?', [(i,) for i in ids])
                conn.commit()
                add_log('system', f'Purged {len(ids)} deleted task(s) permanently')
                bump_event_seq()
                return self._json({'ok': True, 'deleted': len(ids)})

            if p == '/api/deleted-tasks/restore':
                ids = [int(x) for x in (body.get('ids') or [])]
                if not ids:
                    return self._json({'error': 'ids required'}, 400)
                restored = 0
                for i in ids:
                    row = conn.execute('select * from deleted_tasks where id=?', (i,)).fetchone()
                    if not row:
                        continue
                    conn.execute('''
                        insert into tasks (project_id, title, description, status, assignee, due_at, created_at, updated_at)
                        values (?, ?, ?, ?, ?, ?, datetime('now'), datetime('now'))
                    ''', (row['project_id'], row['title'], row['description'], valid_status(row['status']), row['assignee'], row['due_at']))
                    conn.execute('delete from deleted_tasks where id=?', (row['id'],))
                    restored += 1
                conn.commit()
                add_log('david', f'Restored {restored} deleted task(s)')
                bump_event_seq()
                return self._json({'ok': True, 'restored': restored})

            if p == '/api/tasks/update-meta':
                task_id = body.get('task_id')
                if task_id in (None, ''):
                    return self._json({'error': 'task_id required'}, 400)
                team = (body.get('team') or '').strip().lower() or None
                phase = (body.get('phase') or '').strip().lower() or None
                acceptance_criteria = (body.get('acceptance_criteria') or '').strip() or None
                evidence_link = (body.get('evidence_link') or '').strip() or None
                row = conn.execute('select id,title from tasks where id=?', (int(task_id),)).fetchone()
                if not row:
                    return self._json({'error': 'task not found'}, 404)
                conn.execute('update tasks set team=coalesce(?,team), phase=coalesce(?,phase), acceptance_criteria=coalesce(?,acceptance_criteria), evidence_link=coalesce(?,evidence_link) where id=?',
                             (team, phase, acceptance_criteria, evidence_link, int(task_id)))
                conn.commit()
                add_log('system', f"Task metadata updated: {row['title']}")
                bump_event_seq()
                return self._json({'ok': True})

            if p == '/api/tasks/update-status':
                task_id = body.get('task_id')
                status = valid_status(body.get('status') or '', default='')
                if task_id in (None, '') or status not in ALLOWED_STATUS:
                    return self._json({'error': 'task_id and valid status required'}, 400)
                task_row = conn.execute('select id,title,status,assignee,evidence_link from tasks where id=?', (int(task_id),)).fetchone()
                if not task_row:
                    return self._json({'error': 'task not found'}, 404)
                if status == 'completed' and not (task_row['evidence_link'] or '').strip():
                    return self._json({'error': 'evidence_link required before completed. Update task metadata first.'}, 400)
                conn.execute('update tasks set status=? where id=?', (status, int(task_id)))
                conn.commit()
                agent = (task_row['assignee'] or 'aran').strip().lower()
                add_log(agent, f"Task status changed: {task_row['title'] or ('#'+str(task_row['id']))} · {task_row['status'] or '-'} → {status}")
                bump_event_seq()
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
    ensure_dev_db()
    ensure_log_db()
    add_log('system', 'Control dashboard service started')
    srv = ThreadingHTTPServer(('127.0.0.1', 4174), H)
    print('control-dashboard running on http://127.0.0.1:4174')
    srv.serve_forever()
