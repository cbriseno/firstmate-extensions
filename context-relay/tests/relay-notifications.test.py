"""Generic notification adapter/outbox only; no network service."""

import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'lib'))
from fm_context_relay import common, pane_close, relay_notifications as notify


class RelayNotifications(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='relay-notification-')
        self.home = Path(self.temp.name)/'home'
        (self.home/'bin').mkdir(parents=True)
        self.directory = self.home/'data/attempt'
        self.directory.mkdir(parents=True)
        self.paths = common.Paths(str(self.home))
        self.attempt = dict(id='native-attempt', checkpoint_sha256='a'*64)
        self.script = self.home/'bin/event-adapter'
        self.script.write_text('''#!/usr/bin/env python3
import json,sys
from pathlib import Path
value=json.load(sys.stdin);home=Path(value['home']);action=value['action']
with (home/'calls.jsonl').open('a') as f:f.write(json.dumps(value)+'\\n')
if (home/'refuse').exists():
    print('adapter refused',file=sys.stderr);raise SystemExit(1)
if action=='arm':
    (home/'armed').write_text(value['attempt'])
elif action=='failure':
    assert (home/'armed').read_text()==value['attempt']
    assert not (home/'used').exists()
    (home/'used').touch()
elif action=='disarm':
    (home/'disarmed').touch()
else: raise SystemExit(2)
print(json.dumps({'ok':True,'state':'accepted'}))
''')
        self.script.chmod(0o700)
        common.atomic_write_json(self.paths.config, dict(notification_adapter=[str(self.script)]))

    def tearDown(self):
        self.temp.cleanup()

    def test_arm_emit_once_and_disarm_through_generic_adapter(self):
        notify.arm(self.paths, self.directory, self.attempt)
        notify.arm(self.paths, self.directory, self.attempt)
        self.assertFalse((self.home/'used').exists())
        first = notify.emit(self.paths, self.directory, self.attempt, 'launch failed', 'Use explicit recovery once')
        self.assertTrue(first['adapter_accepted'])
        self.assertFalse(first['delivery_confirmed'])
        second = notify.emit(self.paths, self.directory, self.attempt, 'recovery failed', 'Start a fresh primary normally')
        self.assertEqual(len(second['subsequent_failures']), 1)
        calls = [json.loads(line) for line in (self.home/'calls.jsonl').read_text().splitlines()]
        self.assertEqual(sum(row['action'] == 'failure' for row in calls), 1)
        notify.disarm(self.paths, self.directory, self.attempt)
        self.assertTrue((self.home/'disarmed').exists())

    def test_arm_refusal_prevents_retirement_and_cannot_be_rearmed(self):
        (self.home/'refuse').touch()
        with self.assertRaises(pane_close.Preserve):
            notify.arm(self.paths, self.directory, self.attempt)
        self.assertEqual(common.read_json(str(self.directory/'notification-armed.json'))['state'], 'arm-intent')
        with self.assertRaises(pane_close.Preserve):
            notify.arm(self.paths, self.directory, self.attempt)

    def test_emit_refusal_keeps_durable_failure_without_spam(self):
        notify.arm(self.paths, self.directory, self.attempt)
        (self.home/'refuse').touch()
        result = notify.emit(self.paths, self.directory, self.attempt, 'launch uncertain', 'Inspect conserved package')
        self.assertEqual(result['state'], 'adapter-refused-or-uncertain')
        self.assertFalse(result['adapter_accepted'])
        self.assertEqual(common.read_json(str(self.directory/'notification-outbox.json'))['reason'], 'launch uncertain')
        notify.emit(self.paths, self.directory, self.attempt, 'still failed', 'Inspect conserved package')
        self.assertEqual(len((self.home/'calls.jsonl').read_text().splitlines()), 2)

    def test_default_does_not_send_and_retains_console_and_outbox(self):
        common.atomic_write_json(self.paths.config, {})
        self.assertEqual(notify.arm(self.paths, self.directory, self.attempt)['state'], 'disabled')
        result = notify.emit(self.paths, self.directory, self.attempt, 'launch failed', 'Start a new primary normally')
        self.assertEqual(result['state'], 'pending-adapter-disabled')
        self.assertFalse(result['adapter_accepted'])
        self.assertFalse((self.home/'calls.jsonl').exists())
        notify.disarm(self.paths, self.directory, self.attempt)

    def test_adapter_changed_after_arm_is_not_executed(self):
        notify.arm(self.paths, self.directory, self.attempt)
        self.script.write_text(self.script.read_text()+'# changed\n')
        result = notify.emit(self.paths, self.directory, self.attempt, 'launch failed', 'Inspect preserved attempt')
        self.assertEqual(result['state'], 'adapter-refused-or-uncertain')
        self.assertEqual(len((self.home/'calls.jsonl').read_text().splitlines()), 1)


if __name__ == '__main__':
    unittest.main()
