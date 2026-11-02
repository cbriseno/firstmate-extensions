#!/usr/bin/env python3
"""CLI/installed hooks only, private homes, native-shaped fixtures, no models."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

EXT = Path(__file__).resolve().parents[1]
CORE_FILES = ('fm-primary-scope-lib.sh', 'fm-session-lock-lib.sh', 'fm-cursor-lib.sh', 'fm-lock.sh')


def resolve_core():
    configured = os.environ.get('FIRSTMATE_ROOT') or os.environ.get('FM_HOME')
    if not configured:
        raise SystemExit('Set FIRSTMATE_ROOT or FM_HOME to a Firstmate checkout to run these tests.')
    root = Path(configured).expanduser().resolve()
    missing = [name for name in CORE_FILES if not (root/'bin'/name).is_file()]
    if missing:
        raise SystemExit('Incomplete Firstmate core in %s: missing %s. Check FIRSTMATE_ROOT or FM_HOME.' % (root, ', '.join(missing)))
    return root


CORE = resolve_core()
CLI = EXT/'bin/fm-context-relay'
sys.path.insert(0, str(EXT/'lib'))
from fm_context_relay import automatic, common, pane_close


class ThresholdTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='relay-thresholds-')
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.home = self.root/'home with spaces'
        for d in ('bin', 'state', 'config', '.claude', '.agents/skills'):
            (self.home/d).mkdir(parents=True, exist_ok=True)
        (self.home/'.claude/skills').symlink_to('../.agents/skills')
        subprocess.run(['git', '-C', str(self.home), 'init', '-q'], check=True)
        (self.home/'.git/info/exclude').write_text('config/\nstate/\ndata/\n.claude/settings.local.json\n')
        (self.home/'AGENTS.md').write_text('# Fixture\n')
        for name in CORE_FILES:
            shutil.copy2(CORE/'bin'/name, self.home/'bin'/name)
        self.fakebin = self.root/'fakebin'
        self.fakebin.mkdir()
        for name in ('claude', 'codex'):
            binary = self.fakebin/name
            binary.write_text('#!/bin/bash\nif [ "${1:-}" = --version ]; then echo "2.1.316 fixture"; exit 0; fi\nprintf "%s\\n" "$$" > "$TEST_HOME/state/.lock"\nexport CLAUDE_PID=$$\n"$@"\n')
            binary.chmod(0o755)
        self.env = dict(os.environ, PATH=str(self.fakebin)+':'+os.environ['PATH'], FM_LIVE='0', TEST_HOME=str(self.home),
                        CLAUDE_CONFIG_DIR=str(self.root/'private-claude'))
        for name in ('CLAUDE_CODE_SESSION_ID', 'CLAUDE_PID', 'CLAUDE_PROJECT_DIR', 'FM_HOME', 'FM_ROOT_OVERRIDE', 'FM_STATE_OVERRIDE'):
            self.env.pop(name, None)
        self.cfg = self.home/'config/context-relay.json'
        self.cfg.write_text(json.dumps({'precompact_checkpoint': False, 'relay_policy': {
            'claude': {'compactions': 2, 'min_context_percent': 80}, 'codex': {'compactions': 4}}}))
        self.thresholds = self.home/'config/umbrales-relevo'
        self.transcript = self.root/'transcript.jsonl'
        self.backup = self.root/'backup'

    def cli(self, *args, rc=0):
        result = subprocess.run([str(CLI), '--home', str(self.home), *args], env=self.env, text=True, capture_output=True)
        self.assertEqual(result.returncode, rc, result.stdout + result.stderr)
        return result.stdout

    def set_spec(self, spec, harness='claude'):
        self.cli('umbral', 'set', spec, '--harness', harness)

    def hook(self, event='UserPromptSubmit', harness='claude', extra=None, sid='fixture', binary=None):
        payload = {'session_id': sid, 'cwd': str(self.home), 'transcript_path': str(self.transcript), 'hook_event_name': event}
        payload.update(extra or {})
        binary = binary or EXT/'bin/fm-context-relay-hook'
        result = subprocess.run([str(self.fakebin/harness), str(binary), '--home', str(self.home), harness, event],
            input=json.dumps(payload), env=self.env, text=True, capture_output=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        return result.stdout

    def native(self, count=2, tokens=161970, harness='claude'):
        name = 'claude-haiku-boundaries.jsonl' if harness == 'claude' else 'codex-tui-paginated.jsonl'
        rows = [json.loads(r) for r in (EXT/'tests/fixtures'/name).read_text().splitlines()]
        if harness == 'claude':
            rows = rows[:count*2]
            rows.append({'type': 'assistant', 'isSidechain': False, 'timestamp': '2026-11-01T12:00:00Z',
                         'message': {'id': 'last', 'model': 'claude-haiku-5-5', 'usage': {'input_tokens': tokens,
                             'cache_read_input_tokens': 0, 'cache_creation_input_tokens': 0}}})
        self.transcript.write_text(''.join(json.dumps(r)+'\n' for r in rows))

    def record(self, harness='claude', sid='fixture'):
        return json.loads((self.home/('state/context-relay/sessions/%s-%s.json' % (harness, sid))).read_text())

    def test_parser_amounts_percent_and_order(self):
        for value, expected in (('200K', 200000), ('600k', 600000), ('1M', 1000000), ('450000', 450000),
                                ('450,000', 450000), ('450_000', 450000), ('450.000', 450000), ('450 000', 450000), ('1.5m', 1500000)):
            with self.subTest(value=value):
                out = self.cli('umbral', 'check', 'umbral:'+value)
                self.assertEqual(json.loads(out.splitlines()[0])['default']['min_context_tokens'], expected)
        for spec in ('compactaciones:2 umbral:80%', 'umbral:80% compactaciones:2',
                     'compactaciones:2 y umbral:80%', 'umbral:80% AND compactaciones:2'):
            self.assertEqual(json.loads(self.cli('umbral', 'check', spec).splitlines()[0])['default'],
                             {'compactions': 2, 'min_context_percent': 80})
        for spec in ('umbral:600K o compactaciones:5', 'compactaciones:5 OR umbral:600K'):
            self.assertEqual(json.loads(self.cli('umbral', 'check', spec).splitlines()[0])['default'],
                             {'compactions': 5, 'min_context_tokens': 600000, 'operator': 'or'})
        self.assertFalse(self.thresholds.exists())

    def test_rejects_ambiguous_and_contradictory_specs(self):
        for spec in ('', 'umbral:0', 'umbral:101%', 'umbral:NaN%', 'umbral:80%%', 'umbral:4,50',
                     'umbral:80% umbral:600K', 'compactaciones:2 compactaciones:3', 'compactaciones:-1',
                     'compactaciones:2.5', 'o umbral:600K', 'umbral:600K y', 'umbral:600K xor compactaciones:2',
                     'umbral:600K o o compactaciones:2', 'claud: umbral:600K', 'claude: umbral:600K\nclaude: compactaciones:2'):
            with self.subTest(spec=spec):
                out = self.cli('umbral', 'check', spec, rc=2)
                self.assertIn('Ejemplo:', out)
        self.cli('umbral', 'set', 'codex: compactaciones:4', '--harness', 'claude', rc=2)
        self.assertFalse(self.thresholds.exists())

    def test_initial_values_default_precedence_show_reset(self):
        before = self.cfg.read_bytes()
        self.cli('umbral', 'reset')
        self.assertEqual(self.thresholds.read_text(), 'claude: compactaciones:2 umbral:80%\ncodex: compactaciones:4\n')
        self.thresholds.write_text('umbral:600K\nclaude: compactaciones:2 umbral:80%\n')
        out = self.cli('umbral', 'show')
        self.assertIn('codex: umbral:600000 [umbrales-relevo: default]', out)
        self.assertIn('claude: compactaciones:2 umbral:80% [umbrales-relevo: claude]', out)
        self.set_spec('compactaciones:5', 'codex')
        self.assertIn('"compactions": 5', self.cli('umbral', 'show'))
        self.assertEqual(self.cfg.read_bytes(), before)
        self.cli('umbral', 'reset')
        self.assertIn('compactaciones:4', self.thresholds.read_text())
        self.assertIn('Mode: warn', self.cli('umbral', 'show'))
        self.cli('set', 'relay_mode', 'auto', '--reason', 'fixture', '--approved', 'fixture', rc=1)

    def test_tokens_only_unknown_model_no_compactions_and_unique_notice(self):
        self.set_spec('umbral:160K')
        self.native(count=0)
        out = self.hook()
        record = self.record()
        self.assertTrue(record['relay']['pending'])
        self.assertEqual(record['relay']['fired_triggers'], ['umbral'])
        self.assertIn('Trigger: umbral', out)
        self.assertIn('Relay awaiting safe point', out)
        self.assertEqual(self.hook(), '')

    def test_combined_uses_post_compaction_and_configured_window(self):
        self.set_spec('compactaciones:2 umbral:80%')
        self.cfg.write_text(json.dumps({'precompact_checkpoint': False, 'models': {'claude-haiku-5-5': {'window_tokens': 200000}}}))
        self.native(tokens=159999)
        self.hook()
        self.assertFalse(self.record()['relay']['pending'])
        with self.transcript.open('a') as handle:
            handle.write(json.dumps({'type': 'assistant', 'isSidechain': False,
                'message': {'id': 'new-usage', 'model': 'claude-haiku-5-5', 'usage': {'input_tokens': 160000}}})+'\n')
        self.hook()
        record = self.record()['relay']
        self.assertTrue(record['pending'])
        self.assertEqual(record['matched_evidence']['window_source'], 'configured')
        self.assertEqual(record['fired_triggers'], ['compactaciones', 'umbral'])
        evidence = record['matched_evidence']
        # Last boundary has no subsequent usage: never use a pre-boundary high value.
        self.native(tokens=170000)
        with self.transcript.open('a') as handle:
            handle.write(json.dumps({'type': 'system', 'subtype': 'compact_boundary', 'uuid': 'third'})+'\n')
        self.hook('Stop')
        record = self.record()['relay']
        self.assertEqual(record['matched_evidence'], evidence)
        self.assertTrue(record['pending'])
        self.set_spec('compactaciones:4 umbral:80%')
        self.assertEqual(self.hook('Stop'), '')
        self.assertFalse(self.record()['relay']['pending'])

    def test_percentage_unresolved_then_statusline_observed_overrides_configured(self):
        self.set_spec('compactaciones:2 umbral:80%')
        self.native()
        out = self.hook()
        self.assertEqual(self.record()['relay']['status'], 'window_unknown')
        self.assertIn('window unknown', out)
        self.cli('install', '--assisted', '--backup-dir', str(self.backup))
        settings = json.loads((self.home/'.claude/settings.local.json').read_text())
        command = settings['statusLine']['command']
        payload = {'session_id': 'fixture', 'model': {'id': 'claude-haiku-5-5'}, 'context_window': {'context_window_size': 200000}}
        proc = subprocess.run([str(self.fakebin/'claude'), 'bash', '-c', command], input=json.dumps(payload),
            env=self.env, text=True, capture_output=True)
        self.assertEqual((proc.returncode, proc.stdout), (0, ''))
        self.cfg.write_text(json.dumps({'models': {'claude-haiku-5-5': {'window_tokens': 1000000}}, 'relay_mode': 'assisted'}))
        self.hook()
        relay = self.record()['relay']
        self.assertTrue(relay['pending'])
        self.assertEqual(relay['window_source'], 'observed')
        self.assertEqual(relay['window_tokens'], 200000)
        self.assertFalse((self.home/'state/context-relay/active.json').exists())

    def test_or_either_branch_and_order_does_not_choose_priority(self):
        cases = [('umbral:160K o compactaciones:5', 2, 161970, ['umbral']),
                 ('compactaciones:5 o umbral:160K', 2, 161970, ['umbral']),
                 ('umbral:200K o compactaciones:2', 2, 20491, ['compactaciones']),
                 ('umbral:160K o compactaciones:2', 2, 161970, ['compactaciones', 'umbral']),
                 ('compactaciones:2 umbral:160K', 1, 161970, None),
                 ('compactaciones:2 umbral:160K', 2, 161970, ['compactaciones', 'umbral']),
                 ('compactaciones:2', 2, 20491, ['compactaciones'])]
        for index, (spec, count, tokens, fired) in enumerate(cases):
            with self.subTest(spec=spec):
                self.set_spec(spec)
                self.native(count=count, tokens=tokens)
                self.hook(sid='case'+str(index))
                record = self.record(sid='case'+str(index))['relay']
                self.assertEqual(record['pending'], fired is not None)
                self.assertEqual(record['fired_triggers'], fired or [])

    def test_codex_native_count_and_or_with_unverified_other_branch(self):
        self.set_spec('compactaciones:4', 'codex')
        self.native(harness='codex')
        self.hook(harness='codex')
        self.assertTrue(self.record('codex')['relay']['pending'])
        self.assertEqual(self.record('codex')['relay']['fired_triggers'], ['compactaciones'])
        self.set_spec('umbral:30K o compactaciones:5', 'codex')
        self.hook(harness='codex', sid='tokens')
        self.assertEqual(self.record('codex', 'tokens')['relay']['fired_triggers'], ['umbral'])
        rows = [json.loads(r) for r in self.transcript.read_text().splitlines()]
        rows[0]['payload']['originator'] = 'unknown'
        self.transcript.write_text(''.join(json.dumps(r)+'\n' for r in rows))
        self.hook(harness='codex', sid='uncertain')
        self.assertEqual(self.record('codex', 'uncertain')['relay']['fired_triggers'], ['umbral'])
        self.set_spec('compactaciones:4', 'codex')
        self.hook(harness='codex', sid='unverified')
        self.assertFalse(self.record('codex', 'unverified')['relay']['pending'])

    def test_internal_policy_validation_absolute_and_or(self):
        for policy in ({'min_context_tokens': 0}, {'min_context_tokens': 1.5},
                       {'min_context_tokens': True}, {'min_context_tokens': 600000, 'min_context_percent': 80},
                       {'compactions': 2, 'operator': 'or'}, {'min_context_tokens': 200000, 'operator': 'or'},
                       {'compactions': 2, 'min_context_tokens': 200000, 'operator': 'xor'}):
            with self.subTest(policy=policy):
                self.cli('set', 'relay_policy', json.dumps({'claude': policy}), '--reason', 'fixture', '--approved', 'fixture', rc=1)
        self.cli('set', 'relay_policy', '{"claude":{"min_context_tokens":600000}}', '--reason', 'fixture', '--approved', 'fixture')
        self.cli('set', 'relay_policy', '{"claude":{"compactions":2,"min_context_percent":80,"operator":"or"}}', '--reason', 'fixture', '--approved', 'fixture')

    def test_live_edits_bypass_throttle_and_bad_edit_is_explicit(self):
        self.set_spec('umbral:200K')
        self.native(count=0)
        self.hook('PostToolBatch')
        self.thresholds.write_text('claude: umbral:160K\n')
        out = self.hook('PostToolBatch')
        self.assertIn('Trigger: umbral', out)
        self.thresholds.write_text('claude: umbral:80% umbral:70%\n')
        out = self.hook()
        self.assertIn('repeated or conflicting threshold', out)
        self.cli('umbral', 'show', rc=1)
        self.assertTrue(self.record()['relay']['pending'])
        self.assertEqual(self.hook('Stop'), '')
        before = (self.home/'state/context-relay/sessions/claude-fixture.json').read_bytes()
        self.assertEqual(self.hook('Stop', extra={'stop_hook_active': True}), '')
        self.assertEqual((self.home/'state/context-relay/sessions/claude-fixture.json').read_bytes(), before)

    def test_install_exact_backup_rollback_and_foreign_hooks_preserved(self):
        settings = self.home/'.claude/settings.local.json'
        settings.write_text(json.dumps({'permissions': {'allow': ['Read']}, 'hooks': {
            'Stop': [{'hooks': [{'type': 'command', 'command': 'printf supervision'}]}]},
            'statusLine': {'type': 'command', 'command': 'printf original-status', 'padding': 0}}))
        before = {str(p.relative_to(self.home)): p.read_bytes() for p in self.home.rglob('*') if p.is_file()}
        out = self.cli('install', '--assisted', '--dry-run', '--backup-dir', str(self.backup))
        self.assertIn('config/umbrales-relevo', out)
        self.assertIn('controlling-relay-mode/SKILL.md', out)
        self.assertFalse(self.backup.exists())
        self.assertFalse(self.thresholds.exists())
        self.cli('install', '--assisted', '--backup-dir', str(self.backup))
        after = json.loads(settings.read_text())
        self.assertEqual(after['permissions'], {'allow': ['Read']})
        self.assertEqual(len(after['hooks']['Stop']), 2)
        self.assertTrue((self.home/'.agents/skills/preparing-assisted-relay/SKILL.md').exists())
        self.assertTrue((self.home/'.agents/skills/controlling-relay-mode/SKILL.md').exists())
        manifest = json.loads((self.backup/'manifest.json').read_text())
        self.assertTrue(any(e['path'].endswith('/controlling-relay-mode/SKILL.md') for e in manifest['entries']))
        self.assertIn('Mode: assisted', self.cli('umbral', 'show'))
        result = subprocess.run(['bash', '-c', after['statusLine']['command']], input='{}', env=self.env, text=True, capture_output=True)
        self.assertEqual(result.stdout, 'original-status')
        for groups in after['hooks'].values():
            for group in groups:
                for handler in group['hooks']:
                    if handler['command'].endswith('#fm-context-relay'):
                        proc = subprocess.run(['bash', '-c', handler['command']], input='{}', env=self.env, text=True, capture_output=True)
                        self.assertEqual((proc.returncode, proc.stdout, proc.stderr), (0, '', ''))
        proc = subprocess.run(['bash', '-c', after['hooks']['Stop'][0]['hooks'][0]['command']], capture_output=True, text=True)
        self.assertEqual(proc.stdout, 'supervision')
        self.assertFalse((self.home/'state/context-relay/active.json').exists())
        installed_bytes = settings.read_bytes()
        self.cli('install', '--assisted')  # idempotent, no backup needed
        self.assertEqual(settings.read_bytes(), installed_bytes)
        self.cli('rollback', str(self.backup), '--dry-run')
        self.cli('rollback', str(self.backup))
        for relative, data in before.items():
            self.assertEqual((self.home/relative).read_bytes(), data, relative)
        self.assertFalse(self.thresholds.exists())
        self.assertFalse((self.home/'.agents/skills/preparing-assisted-relay/SKILL.md').exists())
        self.assertFalse((self.home/'.agents/skills/controlling-relay-mode/SKILL.md').exists())
        self.cli('rollback', str(self.backup))  # repeated rollback is safe
        self.cli('install', '--assisted', '--backup-dir', str(self.root/'backup-again'))
        self.cli('uninstall')
        self.assertEqual(json.loads(settings.read_text())['statusLine']['command'], 'printf original-status')
        self.assertEqual(len(json.loads(settings.read_text())['hooks']['Stop']), 1)

    def test_mode_skill_backup_restores_existing_bytes_and_rejects_later_edits(self):
        skill = self.home/'.agents/skills/controlling-relay-mode/SKILL.md'
        skill.parent.mkdir()
        skill.write_bytes(b'Original local skill\n')
        skill.chmod(0o640)
        self.cli('install', '--assisted', '--backup-dir', str(self.backup))
        installed = skill.read_bytes()
        skill.write_bytes(b'Later operator edit\n')
        self.cli('rollback', str(self.backup), rc=2)
        self.assertEqual(skill.read_bytes(), b'Later operator edit\n')
        skill.write_bytes(installed)
        self.cli('rollback', str(self.backup))
        self.assertEqual(skill.read_bytes(), b'Original local skill\n')
        self.assertEqual(skill.stat().st_mode & 0o777, 0o640)

    def test_versioned_mode_skill_is_preserved(self):
        skill = self.home/'.agents/skills/controlling-relay-mode/SKILL.md'
        skill.parent.mkdir()
        skill.write_bytes(b'Tracked operator skill\n')
        subprocess.run(['git', '-C', str(self.home), 'add', str(skill)], check=True)
        self.cli('install', '--assisted', '--backup-dir', str(self.backup), rc=2)
        self.assertFalse(self.backup.exists())
        self.assertEqual(skill.read_bytes(), b'Tracked operator skill\n')

    def test_guard_internal_error_fails_open_silent(self):
        active = self.home/'state/context-relay/active.json'
        active.parent.mkdir(parents=True)
        active.write_text('{broken')
        result = subprocess.run([str(EXT/'bin/fm-context-relay-guard'), '--home', str(self.home), 'claude', 'PreToolUse'],
            input='{broken', env=self.env, text=True, capture_output=True)
        self.assertEqual((result.returncode, result.stdout, result.stderr), (0, '', ''))

    def test_guard_deliberate_active_attempt_denial_still_reaches_harness(self):
        directory = self.home/'data/context-relay/attempts/fixture'
        directory.mkdir(parents=True)
        active = self.home/'state/context-relay/active.json'
        active.parent.mkdir(parents=True)
        active.write_text(json.dumps({'directory': str(directory), 'attempt': 'fixture'}))
        (directory/'attempt.json').write_text(json.dumps({'id': 'fixture', 'home': str(self.home), 'state': 'prepared'}))
        (directory/'authorization.json').write_text(json.dumps({'harness': 'claude'}))
        transcript = self.root/'fixture.jsonl'
        transcript.write_text(json.dumps({'sessionId': 'fixture', 'cwd': str(self.home)})+'\n')
        payload = {'session_id': 'fixture', 'transcript_path': str(transcript), 'tool_name': 'Bash',
                   'tool_input': {'command': 'touch forbidden'}}
        result = subprocess.run([str(self.fakebin/'claude'), str(EXT/'bin/fm-context-relay-guard'),
            '--home', str(self.home), 'claude', 'PreToolUse'], input=json.dumps(payload), env=self.env,
            text=True, capture_output=True)
        self.assertEqual((result.returncode, result.stdout), (2, ''))
        self.assertEqual(json.loads(result.stderr)['hookSpecificOutput']['permissionDecision'], 'deny')
        self.assertFalse((self.home/'forbidden').exists())


    def hook_with_window(self, model, sid):
        payload = dict(session_id=sid, transcript_path=str(self.transcript), cwd=str(self.home))
        status = dict(payload, model={'id': model}, context_window={'context_window_size': 1000000})
        # Both events come from one owning fake process, as in the native
        # harness; separate fake invocations intentionally have different PIDs.
        result = subprocess.run([str(self.fakebin/'claude'), 'bash', '-c',
            'printf "%s" "$1" | "$3" --home "$4" claude StatusLine; '
            'printf "%s" "$2" | "$3" --home "$4" claude UserPromptSubmit',
            'fixture', json.dumps(status), json.dumps(payload), str(EXT/'bin/fm-context-relay-hook'), str(self.home)],
            env=self.env, text=True, capture_output=True)
        self.assertEqual(result.returncode, 0, result.stdout+result.stderr)
        return result.stdout

    def test_model_catalog_and_automatic_admission_without_starting_relay(self):
        # Exercise the CLI and hook, then the read-only admission interface.
        # Synthetic provider data proves catalog behavior, not native acceptance.
        self.set_spec('claude: umbral:400000')
        self.cli('auto', 'authorize', '--captain-words', 'isolated fixture authorization',
                 '--reason', 'catalog admission regression', '--min-work-seconds', '1')
        for model, known in (('claude-sonnet-5-5', True), ('claude-unlisted-model', False)):
            with self.subTest(model=model):
                self.native(count=1, tokens=400000)
                rows = [json.loads(line) for line in self.transcript.read_text().splitlines()]
                for row in rows:
                    if row.get('type') == 'assistant':
                        row['message']['model'] = model
                self.transcript.write_text(''.join(json.dumps(row)+'\n' for row in rows))
                out = self.hook(sid=model)
                record = self.record(sid=model)
                self.assertEqual(record['effective']['known_model'], known)
                self.assertEqual(record['compactions']['counting'], 'verified')
                self.assertEqual(record['relay']['policy']['min_context_tokens'], 400000)
                self.assertTrue(record['relay']['pending'])
                self.assertEqual(record['relay']['fired_triggers'], ['umbral'])
                paths = common.Paths(str(self.home))
                notes = dict(useful_work=['Completed the isolated fixture operation'],
                    automatic_safe_point=dict(decisions_saved=True, own_operations_reconciled=True,
                        captain_call_in_progress=False, landing_in_progress=False,
                        critical_operation_in_progress=False))
                operational = dict(snapshot=dict(schema='fm-fleet-snapshot.v1', fm_home=str(self.home),
                    main_inventory=dict(valid=True), tasks=[], backlog=dict(records=[])))
                # A configured catalog window is never enough for automatic
                # admission, including a known model. Observe the owner first.
                with self.assertRaisesRegex(pane_close.Preserve, 'observed_window_missing'):
                    automatic.safe_point(paths, record, notes, operational)
                self.hook_with_window(model, model)
                record = self.record(sid=model)
                self.assertEqual(record['admission']['evidence'], 'observed')
                self.assertTrue(automatic.safe_point(paths, record, notes, operational)['enabled'])
                if known:
                    self.assertNotIn('unknown-model:', str(record['pending']))
                    self.assertEqual(record['effective']['window_tokens'], 1000000)
                    self.assertIsNone(record['effective']['request_tokens'])
                    self.assertIsNone(record['effective']['stop_new_work_tokens'])
                else:
                    self.assertIn('known_model=false', out)
        self.assertEqual(automatic.read_ledger(paths)['attempts'], [])
        self.assertFalse((self.home/'state/context-relay/handoffs').exists())

if __name__ == '__main__':
    unittest.main()
