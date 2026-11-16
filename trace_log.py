"""Optional append-only local event log, separate from recovery checkpoints."""
from contextlib import closing, contextmanager
from contextvars import ContextVar
from datetime import datetime, timezone
import json
import sqlite3

_sink = ContextVar('wearable_trace_sink', default=None)
_state = ContextVar('wearable_trace_state', default=None)


class SQLiteTrace:
    def __init__(self, path, run_id):
        if not run_id:
            raise ValueError('trace requires a run ID')
        self.path, self.run_id = str(path), run_id
        with closing(sqlite3.connect(self.path)) as db, db:
            db.execute('''CREATE TABLE IF NOT EXISTS trace_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT, run_id TEXT NOT NULL,
                timestamp TEXT NOT NULL, state TEXT, event TEXT NOT NULL, data TEXT NOT NULL)''')
            db.execute('CREATE INDEX IF NOT EXISTS trace_run ON trace_events(run_id, id)')

    def write(self, event, data):
        with closing(sqlite3.connect(self.path)) as db, db:
            db.execute('INSERT INTO trace_events(run_id,timestamp,state,event,data) VALUES (?,?,?,?,?)',
                       (self.run_id, datetime.now(timezone.utc).isoformat(), _state.get(), event, json.dumps(data)))


@contextmanager
def trace_context(sink):
    token, state_token = _sink.set(sink), _state.set(None)
    try:
        yield
    finally:
        _state.reset(state_token)
        _sink.reset(token)


def set_state(state):
    _state.set(state)


def emit(event_type, **data):
    sink = _sink.get()
    if sink is not None:
        sink.write(event_type, data)
