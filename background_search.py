"""Persistent search status for short HTTP start/poll requests.

Run one backend process: the worker lives in that process. A restart marks
unfinished searches failed; discovered offers remain in the existing queue.
"""
from concurrent.futures import ThreadPoolExecutor
from hashlib import sha256
import json
import logging
from pathlib import Path
import sqlite3
import threading
import time
import uuid

log = logging.getLogger(__name__)


class SearchRunner:
    def __init__(self, processor, path='work/linkedin_search_runs.sqlite3'):
        self.processor = processor
        self.path = str(path)
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.lock = threading.Lock()
        self.executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix='linkedin-search')
        with self._connect() as db:
            db.execute('CREATE TABLE IF NOT EXISTS search_runs '
                       '(id TEXT PRIMARY KEY, fingerprint TEXT, state TEXT, '
                       'created REAL, updated REAL, result TEXT, error TEXT)')
            db.execute("UPDATE search_runs SET state='failed', error=?, updated=? "
                       "WHERE state IN ('queued','running')",
                       ('Backend restarted during search; retry the search to resume pending offers.', time.time()))

    def _connect(self):
        return sqlite3.connect(self.path, timeout=10)

    def start(self, payload):
        fingerprint = sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()
        now = time.time()
        with self.lock:
            with self._connect() as db:
                row = db.execute("SELECT id FROM search_runs WHERE fingerprint=? AND "
                                 "(state IN ('queued','running') OR (state='completed' AND updated>?)) "
                                 "ORDER BY created DESC LIMIT 1", (fingerprint, now - 300)).fetchone()
                if row:
                    task_id = row[0]
                else:
                    task_id = str(uuid.uuid4())
                    db.execute('INSERT INTO search_runs VALUES (?,?,?,?,?,?,?)',
                               (task_id, fingerprint, 'queued', now, now, None, None))
            if not row:
                self.executor.submit(self._run, task_id, dict(payload))
        return {'task_id': task_id}

    def _run(self, task_id, payload):
        self._update(task_id, 'running')
        log.info('LinkedIn background search %s started', task_id)
        try:
            result = self.processor(payload)
            self._update(task_id, 'completed', result=result)
            log.info('LinkedIn background search %s completed', task_id)
        except Exception as error:
            log.exception('LinkedIn background search %s failed', task_id)
            self._update(task_id, 'failed', error=str(getattr(error, 'detail', error)))

    def _update(self, task_id, state, result=None, error=None):
        with self._connect() as db:
            db.execute('UPDATE search_runs SET state=?, updated=?, result=?, error=? WHERE id=?',
                       (state, time.time(), json.dumps(result) if result is not None else None, error, task_id))

    def status(self, task_id):
        with self._connect() as db:
            row = db.execute('SELECT state, result, error FROM search_runs WHERE id=?', (task_id,)).fetchone()
        if row is None:
            raise KeyError(task_id)
        state, result, error = row
        response = json.loads(result) if result is not None else {}
        response.update(task_id=task_id, state=state)
        if error:
            response['error'] = error
        return response
