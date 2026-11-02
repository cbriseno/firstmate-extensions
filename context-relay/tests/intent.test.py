"""Executable phrase and settings checks in disposable homes, without models."""
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

EXT = Path(__file__).resolve().parents[1]
CLI = EXT/'bin/fm-context-relay'


class RelayIntentTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory(prefix='relay-intent-')
        self.addCleanup(tmp.cleanup)
        self.home = Path(tmp.name)/'renamed home with spaces'
        (self.home/'config').mkdir(parents=True)
        self.env = dict(os.environ, FM_LIVE='0')

    def cli(self, *args):
        result = subprocess.run([str(CLI), '--home', str(self.home), *args],
                                capture_output=True, text=True, env=self.env, timeout=10)
        self.assertEqual(result.returncode, 0, result.stdout+result.stderr)
        return result.stdout

    def snapshot(self):
        return {str(p.relative_to(self.home)): p.read_bytes()
                for p in self.home.rglob('*') if p.is_file()}

    def test_phrase_mapping_is_read_only_and_retains_verbatim_words(self):
        before = self.snapshot()
        cases = json.loads((EXT/'tests/fixtures/relay-phrases.json').read_text())
        for case in cases:
            with self.subTest(message=case['message']):
                result = json.loads(self.cli('relay-intent', '--', case['message']))
                self.assertEqual(result['action'], case['action'])
                self.assertEqual(result['captain_words'], case['message'])
                self.assertFalse(result['executes'])
        self.assertEqual(self.snapshot(), before)
        self.assertFalse((self.home/'state/context-relay/active.json').exists())

    def test_authority_words_status_limits_and_off_through_cli(self):
        self.cli('umbral', 'set', 'codex: umbral:600K or compactaciones:5')
        before = self.snapshot()
        status = self.cli('auto', 'status')
        self.assertIn('not authorized. Mode: warn', status)
        self.assertIn('codex: compactaciones:5 o umbral:600000', status)
        self.assertIn('none recorded', status)
        self.assertEqual(self.snapshot(), before)
        # Route only synthetic current instructions; the classifier never executes.
        words = '  ¡ACTIVA EL RELEVO AUTOMÁTICO, por favor!\n'
        routed = json.loads(self.cli('relay-intent', '--', words))
        self.assertEqual(routed['action'], 'enable')
        auth = json.loads(self.cli('auto', 'authorize', '--captain-words', routed['captain_words'],
                                   '--reason', 'Synthetic current captain instruction',
                                   '--window-seconds', '1800', '--daily-cap', '4'))
        self.assertEqual(auth['authorization']['captain_words'], words)
        shown = json.loads(self.cli('auto', 'status', '--json'))
        self.assertTrue(shown['auto_enabled'])
        self.assertEqual(shown['authority']['captain_words'], words)
        self.assertEqual(shown['limits']['window_seconds'], 1800)
        self.assertEqual(shown['limits']['daily_cap'], 4)
        self.assertEqual(shown['thresholds']['policies']['codex']['operator'], 'or')
        ledger = self.home/'state/context-relay/automatic-ledger.json'
        ledger.write_text(json.dumps(dict(schema='fm-auto-ledger.v1', requests={}, attempts=[
            dict(id='synthetic-attempt', at='2026-11-01T12:00:00Z', state='accepted', accepted=True)])))
        before = self.snapshot()
        self.assertIn('2026-11-01T12:00:00Z: accepted', self.cli('auto', 'status'))
        self.assertIn('autorizado. Modo: auto', self.cli('auto', 'status', '--language', 'es'))
        self.assertEqual(self.snapshot(), before)
        off = json.loads(self.cli('auto', 'off', '--mode', 'assisted'))
        self.assertFalse(off['enabled'])
        self.assertIn('not authorized. Mode: assisted', self.cli('auto', 'status'))
        self.assertFalse((self.home/'state/context-relay/active.json').exists())


if __name__ == '__main__':
    unittest.main()
