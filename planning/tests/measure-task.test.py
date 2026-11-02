#!/usr/bin/env python3
import json
import subprocess
import tempfile
import unittest
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / 'bin/measure-task'


class MeasurementTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.sessions = self.root / 'sessions'
        self.sessions.mkdir()
        self.home = self.root / 'home'
        (self.home / 'state').mkdir(parents=True)
        (self.home / 'data/t1').mkdir(parents=True)
        (self.home / 'data/t1/brief.md').write_text('planning_mode: speckit\n')
        (self.home / 'state/t1.meta').write_text('spawned_at=2026-01-01T00:00:00Z\n')
        (self.home / 'state/t1.status').write_text('done [at=1767225660]: done\n')
        (self.home / 'state/t1.codex-session.g1.json').write_text(json.dumps({'session': 's1'}))
        self.events = [
            {'type': 'session_meta', 'payload': {'id': 's1'}},
            {'type': 'event_msg', 'timestamp': '2026-01-01T00:00:01Z', 'payload': {'type': 'task_started'}},
            {'type': 'response_item', 'payload': {'type': 'function_call', 'name': 'exec_command', 'arguments': json.dumps({'cmd': 'cat src/a'})}},
            {'type': 'compacted', 'payload': {}},
            {'type': 'response_item', 'payload': {'type': 'function_call', 'name': 'exec_command', 'arguments': json.dumps({'cmd': 'cat src/a src/b'})}},
            {'type': 'event_msg', 'payload': {'type': 'token_count', 'info': {'total_token_usage': {'input_tokens': 100, 'output_tokens': 20, 'cached_input_tokens': 30, 'reasoning_output_tokens': 5}}}},
            {'type': 'event_msg', 'timestamp': '2026-01-01T00:00:11Z', 'payload': {'type': 'task_complete'}},
        ]
        self.write_events()
        self.addCleanup(self.tmp.cleanup)

    def write_events(self):
        (self.sessions / 'rollout.jsonl').write_text(''.join(json.dumps(e) + '\n' for e in self.events))

    def run_report(self):
        return subprocess.run([str(SCRIPT), '--home', str(self.home), '--sessions-dir', str(self.sessions), 't1'], text=True, capture_output=True)

    def test_task_records_and_counters(self):
        proc = self.run_report()
        self.assertEqual(proc.returncode, 0, proc.stderr)
        data = json.loads(proc.stdout)
        self.assertEqual((data['planning_mode'], data['wall_seconds'], data['active_turn_seconds']), ('speckit', 60, 10))
        self.assertEqual((data['turns'], data['tool_calls'], data['compactions']), (1, 2, 1))
        self.assertEqual((data['post_compaction_reads'], data['post_compaction_repeated_reads']), (2, 1))
        self.assertEqual(data['tokens']['input_tokens'], 100)
        self.assertEqual(data['resumes'], 'unavailable')

    def test_missing_duplicate_and_partial_rollout(self):
        (self.sessions / 'second.jsonl').write_text((self.sessions / 'rollout.jsonl').read_text())
        self.assertEqual(json.loads(self.run_report().stdout)['turns'], 'unavailable')
        (self.sessions / 'second.jsonl').unlink()
        self.events.pop()
        self.write_events()
        self.assertEqual(json.loads(self.run_report().stdout)['active_turn_seconds'], 'unavailable')
        (self.sessions / 'rollout.jsonl').unlink()
        self.assertEqual(json.loads(self.run_report().stdout)['tokens'], 'unavailable')

    def test_missing_and_decreasing_usage_and_bad_mode(self):
        self.events.insert(-1, {'type': 'event_msg', 'payload': {'type': 'token_count', 'info': {'total_token_usage': {'input_tokens': 1}}}})
        self.write_events()
        data = json.loads(self.run_report().stdout)
        self.assertEqual(data['tokens']['input_tokens'], 'unavailable')
        self.assertEqual(data['tokens']['output_tokens'], 'unavailable')
        (self.home / 'data/t1/brief.md').write_text('planning_mode: bad\n')
        self.assertNotEqual(self.run_report().returncode, 0)


if __name__ == '__main__':
    unittest.main()
