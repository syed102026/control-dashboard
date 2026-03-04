#!/usr/bin/env python3
import json
import sqlite3
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

BASE_DIR = Path(__file__).resolve().parent
DB_PATH = Path('/Users/openclaw/Desktop/March2026/databases/dev.db')


def q(sql, params=()):
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
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

    def do_GET(self):
        p = urlparse(self.path).path
        if p == '/api/overview':
            cats = q('select count(*) as c from categories')[0]['c']
            projs = q('select count(*) as c from projects')[0]['c']
            tasks = q('select count(*) as c from tasks')[0]['c']
            return self._json({'categories': cats, 'projects': projs, 'tasks': tasks})
        if p == '/api/categories':
            return self._json(q('select id,name,description,created_at,updated_at from categories order by id'))
        if p == '/api/projects':
            return self._json(q('''
                select p.id,p.name,p.status,p.priority,p.category_id,c.name as category_name,p.created_at,p.updated_at
                from projects p left join categories c on c.id=p.category_id order by p.id
            '''))
        if p == '/api/tasks':
            return self._json(q('''
                select t.id,t.title,t.description,t.status,t.assignee,t.due_at,t.project_id,p.name as project_name,t.created_at,t.updated_at
                from tasks t left join projects p on p.id=t.project_id order by t.id
            '''))
        if p == '/' or p == '/index.html':
            html = (BASE_DIR / 'index.html').read_bytes()
            self.send_response(200)
            self.send_header('Content-Type', 'text/html; charset=utf-8')
            self.send_header('Content-Length', str(len(html)))
            self.end_headers()
            self.wfile.write(html)
            return
        self._json({'error': 'not found'}, 404)


if __name__ == '__main__':
    if not DB_PATH.exists():
        raise SystemExit(f'dev.db not found at {DB_PATH}')
    srv = ThreadingHTTPServer(('127.0.0.1', 4174), H)
    print('control-dashboard running on http://127.0.0.1:4174')
    srv.serve_forever()
