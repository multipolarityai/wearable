"""SQLite checkpoints with optimistic concurrency and explicit interrupted-call recovery."""
from contextlib import closing
import hashlib
import json
import sqlite3
from collections.abc import Mapping


class CheckpointError(ValueError):
    pass


def plain(value):
    if isinstance(value, Mapping):
        return {k: plain(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [plain(v) for v in value]
    return value


def fingerprint(value):
    return hashlib.sha256(json.dumps(plain(value), sort_keys=True, separators=(',', ':')).encode()).hexdigest()


class Checkpoint:
    """One run handle. New handles must reload before writing an existing run."""
    def __init__(self, path, run_id):
        if not run_id:
            raise CheckpointError('run ID must not be empty')
        self.path, self.run_id, self.revision = str(path), run_id, None
        with closing(sqlite3.connect(self.path)) as db, db:
            db.execute('''CREATE TABLE IF NOT EXISTS runs (
                id TEXT PRIMARY KEY, fingerprint TEXT NOT NULL,
                revision INTEGER NOT NULL, snapshot TEXT NOT NULL)''')

    def start(self, signature, initial, *, resume=False, retry_pending=False):
        if retry_pending and not resume:
            raise CheckpointError('retry_pending requires resume')
        with closing(sqlite3.connect(self.path)) as db, db:
            row = db.execute('SELECT fingerprint, revision, snapshot FROM runs WHERE id=?',
                             (self.run_id,)).fetchone()
            if row is None:
                if resume:
                    raise CheckpointError('run ID does not exist')
                try:
                    db.execute('INSERT INTO runs VALUES (?, ?, 0, ?)',
                               (self.run_id, signature, json.dumps(initial)))
                except sqlite3.IntegrityError as exc:
                    raise CheckpointError('run ID already exists; choose another ID') from exc
                self.revision = 0
                return initial
            if not resume:
                raise CheckpointError('run ID already exists; use resume or another ID')
            if row[0] != signature:
                raise CheckpointError('plan, adapter configuration or input differs from checkpoint')
            snapshot = json.loads(row[2])
            if snapshot['phase'] == 'pending' and not retry_pending:
                raise CheckpointError('interrupted call: inspect its side effects, then explicitly retry_pending if safe')
            self.revision = row[1]
            if snapshot['phase'] == 'pending':
                # The pending snapshot stores pre-call data and the reserved step.
                # A retry consumes another step; interrupted calls still count.
                snapshot['phase'] = 'retry'
            return snapshot

    def save(self, snapshot):
        with closing(sqlite3.connect(self.path)) as db, db:
            result = db.execute('UPDATE runs SET revision=revision+1, snapshot=? WHERE id=? AND revision=?',
                                (json.dumps(snapshot), self.run_id, self.revision))
            if result.rowcount != 1:
                raise CheckpointError('run was changed by another runner; stop this runner')
        self.revision += 1
