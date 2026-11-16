import asyncio
from contextlib import closing
from dataclasses import replace
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest

from checkpoints import Checkpoint
from stub_tools import REGISTRY
from trace_log import SQLiteTrace
from wearable import load_plan, run

ROOT = Path(__file__).resolve().parents[1] / 'examples'


class TraceTests(unittest.TestCase):
    def test_append_only_resume_and_metadata(self):
        plan = load_plan(ROOT / 'agent.yaml', ROOT / 'tools.yaml', ROOT / 'runtime.yaml', REGISTRY)
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'run.sqlite'
            for resume in (False, True):
                asyncio.run(run(plan, {'request': 'PRIVATE PROMPT'}, REGISTRY,
                                checkpoint=Checkpoint(path, 'demo'), resume=resume,
                                trace_sink=SQLiteTrace(path, 'demo')))
            with closing(sqlite3.connect(path)) as db:
                rows = db.execute('SELECT event,data FROM trace_events ORDER BY id').fetchall()
            events = [r[0] for r in rows]
            self.assertEqual(events.count('action.started'), 4)
            self.assertEqual(events.count('run.completed'), 2)
            self.assertEqual(events.count('checkpoint.reused'), 1)
            self.assertNotIn('PRIVATE PROMPT', str(rows))
            for kind, data in rows:
                if kind == 'action.completed':
                    self.assertGreaterEqual(json.loads(data)['elapsed_ms'], 0)

    def test_failure_and_run_isolation(self):
        plan = load_plan(ROOT / 'agent.yaml', ROOT / 'tools.yaml', ROOT / 'runtime.yaml', REGISTRY)
        async def cancelled(args):
            raise asyncio.CancelledError()
        registry = {**REGISTRY, 'llm.draft': replace(REGISTRY['llm.draft'], call=cancelled)}
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'trace.sqlite'
            with self.assertRaises(asyncio.CancelledError):
                asyncio.run(run(plan, {'request': 'x'}, registry, trace_sink=SQLiteTrace(path, 'cancelled')))
            asyncio.run(run(plan, {'request': 'x'}, REGISTRY, trace_sink=SQLiteTrace(path, 'next')))
            with closing(sqlite3.connect(path)) as db:
                row = db.execute("SELECT data FROM trace_events WHERE event='run.failed'").fetchone()
                state = db.execute("SELECT state FROM trace_events WHERE event='run.started' AND run_id='next'").fetchone()[0]
            self.assertEqual(json.loads(row[0])['error'], 'CancelledError')
            self.assertIsNone(state)
