"""Token-free safety regressions; native automatic acceptance needs live labs."""

import concurrent.futures
import datetime
import json
import copy
import shutil
from pathlib import Path
import sys
import subprocess
import tempfile
import unittest
from unittest import mock
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'lib'))
from fm_context_relay import automatic as auto, common, pane_close, measure, admission, safety_manifest


class AutomaticSafety(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='fm-auto-')
        self.home = Path(self.tmp.name)/'home'
        self.paths = common.Paths(str(self.home))
        (self.home/'config').mkdir(parents=True)
        (self.home/'state').mkdir()
        self.now = common.utcnow().replace(microsecond=0)
        self.record = dict(harness='codex', session_id='native-one',
                           effective=dict(known_model=True), scan=dict(caught_up=True),
                           session_started_at=common.iso(self.now-datetime.timedelta(minutes=10)),
                           relay=dict(pending=True, pending_since=common.iso(self.now),
                                      policy=dict(harness='codex', compactions=4, min_context_percent=None),
                                      matched_evidence=dict(compactions=4, counting='verified')))
        self.record.update(model='gpt-unlisted', owner_pid='123', transcript_path='/fake/rollout',
            window_tokens_observed=475000,
            window_observation=dict(harness='codex', session_id='native-one', owner_pid='123',
                model='gpt-unlisted', window_tokens=475000, source='codex-token-count',
                transcript_path='/fake/rollout', dev=1, ino=1, line=10, at=common.iso(self.now)))
        self.record['scan'].update(session_id='native-one', path='/fake/rollout', dev=1, ino=1,
            window_line=10, window_tokens=475000, source='cli', codex_invalid=False,
            compaction_ids=['w1','w2','w3','w4'], codex_tui=dict(windows=dict(w1=[],w2=[],w3=[],w4=[])))
        counting, signal = measure.compaction_counting('codex', self.record['scan'])
        self.record['compactions'] = dict(count=4, counting=counting, signal=signal)
        self.notes = dict(useful_work=['Completed the durable requested operation'],
                          automatic_safe_point=dict(decisions_saved=True, own_operations_reconciled=True,
                              captain_call_in_progress=False, landing_in_progress=False,
                              critical_operation_in_progress=False))
        self.operational = dict(snapshot=dict(schema='fm-fleet-snapshot.v1', fm_home=str(self.home),
                                              main_inventory=dict(valid=True), tasks=[],
                                              backlog=dict(records=[])))

    def tearDown(self):
        self.tmp.cleanup()

    def enabled(self, **selected):
        if not Path(self.paths.config).exists():
            common.atomic_write_json(self.paths.config, dict(relay_policy={'codex': {'compactions': 4}}))
        return auto.set_mode(self.paths, 'auto', 'Autorizo el relevo automático con el mismo harness.',
                             'explicit isolated test', selected)

    def request(self):
        return auto.notice(self.paths, self.record, self.now)

    def reserve(self):
        return auto.reserve(self.paths, self.record, self.notes, self.operational, self.now)

    def test_authority_rereads_after_configuration_check(self):
        loaded = SimpleNamespace(status='ok', settings=SimpleNamespace(value=lambda key: 'auto'))
        with mock.patch.object(auto, 'read_authority', side_effect=[{'id': 'old'}, {'id': 'new'}]), \
             mock.patch.object(auto.config, 'load', return_value=loaded):
            self.assertEqual(auto.authority(self.paths)['id'], 'new')

    def test_absolute_codex_policy_does_not_warn_about_required_counting(self):
        errors, warnings = auto.config.validate(dict(models={}, relay_policy={'codex': {'min_context_tokens': 400000}}))
        self.assertEqual((errors, warnings), ([], []))

    def test_defaults_stay_off(self):
        self.assertEqual(auto.view(self.paths)['mode'], 'warn')
        self.assertFalse(auto.view(self.paths)['auto_enabled'])
        self.assertIsNone(self.request())
        self.assertFalse(auto.locations(self.paths)['control'].exists())

    def test_auto_file_without_explicit_record_cannot_authorize(self):
        common.atomic_write_json(self.paths.config, dict(relay_mode='auto'))
        with self.assertRaises(pane_close.Preserve):
            auto.authority(self.paths)
        self.assertFalse(auto.view(self.paths)['auto_enabled'])

    def test_words_and_reason_required(self):
        for words, reason in [('', 'test'), ('yes', ''), (None, 'test')]:
            with self.subTest(words=words, reason=reason), self.assertRaises(pane_close.Preserve):
                auto.set_mode(self.paths, 'auto', words, reason)
        self.assertFalse(auto.locations(self.paths)['control'].exists())

    def test_explicit_authority_retains_words_and_thresholds(self):
        before = dict(relay_mode='warn', relay_policy={'codex': {'compactions': 4}})
        common.atomic_write_json(self.paths.config, before)
        result = self.enabled()
        self.assertTrue(result['enabled'])
        auth = auto.authority(self.paths)
        self.assertEqual(auth['captain_words'], 'Autorizo el relevo automático con el mismo harness.')
        self.assertEqual(auth['limits']['window_seconds'], 1800)
        current = common.read_json(self.paths.config)
        self.assertEqual({k: v for k, v in current.items() if k != 'relay_mode'},
                         {k: v for k, v in before.items() if k != 'relay_mode'})
        self.assertEqual(auto.locations(self.paths)['control'].stat().st_mode & 0o777, 0o600)

    def test_kill_switch_honored_before_any_action(self):
        self.enabled()
        self.request()
        for mode in ('assisted', 'warn'):
            with self.subTest(mode=mode):
                auto.set_mode(self.paths, mode)
                self.assertIsNone(self.request())
                with self.assertRaises(pane_close.Preserve):
                    self.reserve()
                self.assertEqual(auto.read_ledger(self.paths)['attempts'], [])

    def test_kill_switch_survives_broken_config_and_auto_fallback(self):
        self.enabled()
        (self.home/'config/context-relay.json').write_text('{broken')
        result = auto.set_mode(self.paths, 'warn')
        self.assertTrue(result['config_preserved'])
        self.assertFalse(auto.view(self.paths)['auto_enabled'])
        common.atomic_write_json(self.paths.config, dict(relay_mode='auto'))
        with self.assertRaises(pane_close.Preserve):
            auto.authority(self.paths)

    def test_limits_are_bounded(self):
        for values in ({'daily_cap': 25}, {'daily_cap': True}, {'min_work_seconds': 0},
                       {'window_seconds': 59}, {'safe_wait_seconds': 3601}, {'typo': 1}):
            with self.subTest(values=values), self.assertRaises(pane_close.Preserve):
                auto.limits(values)

    def test_notice_and_double_trigger_reserve_once(self):
        self.enabled()
        self.assertIn('Automatic relay authorized', self.request())
        self.assertIsNone(self.request())
        def claim(_):
            try:
                return self.reserve()['id']
            except pane_close.Preserve:
                return None
        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
            results = list(pool.map(claim, range(8)))
        self.assertEqual(sum(r is not None for r in results), 1)
        self.assertEqual(len(auto.read_ledger(self.paths)['attempts']), 1)

    def test_unsafe_moments_wait_without_reserving(self):
        self.enabled()
        self.request()
        changes = [dict(captain_call_in_progress=True), dict(landing_in_progress=True),
                   dict(decisions_saved=False), dict(critical_operation_in_progress=True),
                   dict(own_operations_reconciled=False)]
        original = dict(self.notes['automatic_safe_point'])
        for change in changes:
            self.notes['automatic_safe_point'] = dict(original, **change)
            with self.subTest(change=change), self.assertRaises(pane_close.Preserve):
                self.reserve()
        self.notes['automatic_safe_point'] = original
        self.operational['snapshot']['tasks'] = [dict(id='critical-worker', current_state=dict(state='working'))]
        with self.assertRaises(pane_close.Preserve):
            self.reserve()
        self.assertEqual(auto.read_ledger(self.paths)['attempts'], [])
        self.operational['snapshot']['tasks'] = []
        self.assertEqual(self.reserve()['state'], 'reserved')

    def test_control_lock_blocks_active_answer_but_pending_calls_travel(self):
        self.enabled()
        self.request()
        (self.home/'state/.control-landing.lock').mkdir()
        with self.assertRaises(pane_close.Preserve):
            self.reserve()
        (self.home/'state/.control-landing.lock').rmdir()
        self.operational['snapshot']['backlog']['records'] = [dict(state='in_flight', call={'question': 'Captain response'})]
        self.assertEqual(self.reserve()['state'], 'reserved')

    def test_worker_pause_requires_exact_safe_point_acknowledgement(self):
        self.enabled()
        self.request()
        self.operational['snapshot']['tasks'] = [dict(id='worker', current_state=dict(state='paused'))]
        status = self.home/'state/worker.status'
        status.write_text('paused [at=123]: background work is still in flight\n')
        with self.assertRaisesRegex(pane_close.Preserve, 'acknowledged'):
            self.reserve()
        key = 'auto-relay-'+auto.request_key(self.record)[:16]
        status.write_text('paused [key='+key+'] [at='+str(int(self.now.timestamp()))+']: safe point reached\n')
        self.assertEqual(self.reserve()['state'], 'reserved')


    def test_core_change_invalidates_standing_authority(self):
        self.enabled()
        (self.home/'bin').mkdir()
        (self.home/'bin/fm-lock.sh').write_text('# changed core primitive\n')
        self.assertFalse(auto.view(self.paths)['auto_enabled'])

    def test_kill_switch_does_not_wait_for_a_busy_settings_writer(self):
        self.enabled()
        with common.FileLock(self.paths.config_lock):
            result = auto.set_mode(self.paths, 'warn')
        self.assertFalse(result['enabled'])
        self.assertTrue(result['config_preserved'])
        self.assertFalse(auto.view(self.paths)['auto_enabled'])

    def test_safe_wait_is_bounded(self):
        self.enabled(safe_wait_seconds=5)
        self.request()
        later = self.now+datetime.timedelta(seconds=6)
        with self.assertRaisesRegex(pane_close.Preserve, 'deadline'):
            auto.reserve(self.paths, self.record, self.notes, self.operational, later)
        self.assertEqual(auto.read_ledger(self.paths)['attempts'], [])
        self.assertIn('Safe-point deadline expired', auto.notice(self.paths, self.record, later))
        self.assertIsNone(auto.notice(self.paths, self.record, later))
        self.assertEqual(len(list((Path(self.paths.data_dir)/'automatic-outbox').glob('*.json'))), 1)

    def test_minimum_useful_work_and_age(self):
        self.enabled()
        self.request()
        self.notes['useful_work'] = []
        with self.assertRaisesRegex(pane_close.Preserve, 'useful work'):
            self.reserve()
        self.notes['useful_work'] = ['Finished task']
        self.record['session_started_at'] = common.iso(self.now)
        with self.assertRaisesRegex(pane_close.Preserve, 'minimum'):
            self.reserve()

    def next_request(self, seconds=1900):
        self.now += datetime.timedelta(seconds=seconds)
        self.record['session_id'] += '-new'
        self.record['scan']['session_id'] = self.record['session_id']
        self.record['window_observation']['session_id'] = self.record['session_id']
        self.record['relay']['pending_since'] = common.iso(self.now)
        self.request()

    def test_predecessor_never_accepted_prevents_chain(self):
        self.enabled()
        self.request()
        self.reserve()
        self.next_request()
        with self.assertRaisesRegex(pane_close.Preserve, 'never accepted'):
            self.reserve()

    def test_window_limit_and_daily_cap(self):
        self.enabled(daily_cap=2)
        self.request()
        first = self.reserve()
        auto.update(self.paths, first['id'], 'accepted', accepted=True)
        self.next_request(100)
        with self.assertRaisesRegex(pane_close.Preserve, 'window'):
            self.reserve()
        self.now += datetime.timedelta(seconds=1800)
        # The expired safe-point request cannot be silently renewed.
        self.next_request(1)
        second = self.reserve()
        auto.update(self.paths, second['id'], 'accepted', accepted=True)
        self.next_request()
        with self.assertRaisesRegex(pane_close.Preserve, 'daily relay cap'):
            self.reserve()

    def test_corrupt_ledger_does_not_reset_history(self):
        self.enabled()
        auto.locations(self.paths)['ledger'].write_text('{broken')
        with self.assertRaisesRegex(pane_close.Preserve, 'ledger'):
            self.request()

    def test_machine_settings_read_write_through_cli_only(self):
        cli = Path(__file__).resolve().parents[1]/'bin/fm-context-relay'
        def call(*args):
            p = subprocess.run([str(cli), '--home', str(self.home), *args],
                               capture_output=True, text=True, timeout=10)
            self.assertEqual(p.returncode, 0, p.stdout+p.stderr)
            return json.loads(p.stdout)
        changed = call('umbral', 'set', 'codex: umbral:600K o compactaciones:5', '--json')
        self.assertTrue(changed['written'])
        self.assertEqual(changed['policies']['codex']['min_context_tokens'], 600000)
        self.assertEqual(changed['policies']['codex']['operator'], 'or')
        shown = call('umbral', 'show', '--json')
        self.assertEqual(shown['schema'], 'fm-relay-thresholds.v1')
        before = (self.home/'config/umbrales-relevo').read_bytes()
        authorized = call('auto', 'authorize', '--captain-words', 'Autorizo el relevo automático', '--reason', 'prueba privada')
        self.assertTrue(authorized['enabled'])
        state = call('auto', 'show', '--limit', '1')
        self.assertTrue(state['auto_enabled'])
        self.assertEqual(state['authority']['captain_words'], 'Autorizo el relevo automático')
        call('auto', 'off', '--mode', 'assisted')
        view = call('show', '--json')
        self.assertEqual(view['schema'], 'fm-context-relay-settings.v1')
        self.assertEqual(view['automatic']['mode'], 'assisted')
        self.assertFalse(view['automatic']['auto_enabled'])
        self.assertEqual((self.home/'config/umbrales-relevo').read_bytes(), before)


    def test_unlisted_model_with_observation_and_incomplete_measurement(self):
        self.enabled()
        self.request()
        self.record['effective']['known_model'] = False
        self.assertTrue(auto.safe_point(self.paths, self.record, self.notes, self.operational, self.now)['enabled'])
        self.record['window_observation'] = {}
        with self.assertRaisesRegex(pane_close.Preserve, 'observed_window_missing'):
            self.reserve()
        self.record['scan']['caught_up'] = False
        with self.assertRaisesRegex(pane_close.Preserve, 'measurement_incomplete'):
            self.reserve()

    def test_observation_and_counting_admission_matrix(self):
        self.record['effective']['known_model'] = False
        policy = self.record['relay']['policy']
        self.assertTrue(admission.evidence(self.record, policy)['admitted'])
        for field, value in [('session_id', 'other'), ('owner_pid', '456'),
                             ('model', 'other-model'), ('source', 'configured'),
                             ('window_tokens', 0), ('at', None), ('ino', 999)]:
            record = copy.deepcopy(self.record)
            record['window_observation'][field] = value
            with self.subTest(field=field):
                self.assertFalse(admission.evidence(record, policy)['admitted'])
        record = copy.deepcopy(self.record)
        record['model_changed_at'] = common.iso(self.now+datetime.timedelta(seconds=1))
        self.assertEqual(admission.evidence(record, policy)['reason_code'], 'observed_window_stale')
        record['model_changed_at'] = None
        record['scan']['codex_invalid'] = True
        self.assertEqual(admission.evidence(record, policy)['reason_code'], 'compaction_counting_unverified')
        absolute = dict(harness='codex', min_context_tokens=400000)
        self.assertTrue(admission.evidence(record, absolute)['admitted'])
        self.assertEqual(admission.evidence(record, None)['reason_code'], 'policy_unresolved')
        record['window_observation'] = {}
        self.assertEqual(admission.evidence(record, absolute)['reason_code'], 'observed_window_missing')

    def test_claude_unknown_model_uses_boundary_signal(self):
        record = copy.deepcopy(self.record)
        record.update(harness='claude', model='claude-new')
        record['window_observation'].update(harness='claude', model='claude-new', source='claude-statusline')
        record['compactions']['signal'] = 'compact_boundary'
        self.assertTrue(admission.evidence(record, dict(harness='claude', compactions=4))['admitted'])
        record['compactions']['signal'] = 'PreCompact'
        self.assertEqual(admission.evidence(record, dict(harness='claude', compactions=4))['reason_code'],
                         'compaction_counting_unverified')

    def test_manifest_coverage_rejects_an_unclassified_module(self):
        root = Path(__file__).resolve().parents[1]
        self.assertEqual(safety_manifest.uncovered(root), [])
        clone = Path(self.tmp.name)/'extension'
        shutil.copytree(root, clone, ignore=shutil.ignore_patterns('__pycache__'))
        (clone/'lib/fm_context_relay/new_retire.py').write_text('# new critical module\n')
        with self.assertRaisesRegex(ValueError, 'new_retire.py'):
            safety_manifest.fingerprint(self.paths, clone)

    def test_binding_mutation_matrix_in_disposable_runtime(self):
        root = Path(__file__).resolve().parents[1]
        clone = Path(self.tmp.name)/'extension'
        shutil.copytree(root, clone, ignore=shutil.ignore_patterns('__pycache__'))
        original = safety_manifest.fingerprint(self.paths, clone)
        for relative in safety_manifest.EXTENSION_FILES:
            target = clone/relative
            before = target.read_bytes()
            target.write_bytes(before+b'\n')
            with self.subTest(relative=relative):
                changes = safety_manifest.changed(original, safety_manifest.fingerprint(self.paths, clone))
                self.assertIn('extension/'+relative, changes)
            target.write_bytes(before)
        for relative in safety_manifest.CORE_FILES:
            target = self.home/relative
            target.parent.mkdir(exist_ok=True)
            target.write_text('# changed dependency\n')
            with self.subTest(relative=relative):
                self.assertIn('firstmate/'+relative,
                              safety_manifest.changed(original, safety_manifest.fingerprint(self.paths, clone)))
            target.unlink()
        for relative in ('README.md', 'compatibility.json', 'model-catalog.json',
                         'tests/automatic.test.py', 'context-relay.example.json'):
            target = clone/relative
            before = target.read_bytes()
            target.write_bytes(before+b'\n')
            with self.subTest(relative=relative):
                self.assertEqual(safety_manifest.fingerprint(self.paths, clone), original)
            target.write_bytes(before)
        newer = dict(original, protocol_version=original['protocol_version']+1)
        self.assertIn('protocol_version', safety_manifest.changed(original, newer))
        self.assertIn('legacy', safety_manifest.changed({}, original)[0])

    def test_status_and_notice_name_changed_dependency(self):
        self.enabled()
        (self.home/'bin').mkdir()
        (self.home/'bin/fm-lock-release.py').write_text('# replacement\n')
        result = auto.view(self.paths)
        self.assertFalse(result['auto_enabled'])
        self.assertIn('firstmate/bin/fm-lock-release.py', result['reason'])
        self.assertIn('firstmate/bin/fm-lock-release.py', self.request())
        self.assertIsNone(self.request())

    def test_core_revision_binding_is_preserved_for_transitive_changes(self):
        subprocess.run(['git', '-C', str(self.home), 'init', '-q'], check=True)
        dependency = self.home/'core-dependency.txt'
        def commit(text):
            dependency.write_text(text)
            subprocess.run(['git', '-C', str(self.home), 'add', dependency.name], check=True)
            subprocess.run(['git', '-C', str(self.home), '-c', 'user.name=Fixture',
                            '-c', 'user.email=fixture@example.invalid', 'commit', '-qm', 'fixture'], check=True)
        commit('v1')
        self.enabled()
        self.assertTrue(auto.view(self.paths)['auto_enabled'])
        commit('v2')
        result = auto.view(self.paths)
        self.assertFalse(result['auto_enabled'])
        self.assertIn('firstmate_revision', result['reason'])
        self.assertIn('firstmate_revision', self.request())

    def test_legacy_authorization_is_not_migrated(self):
        self.enabled()
        target = auto.locations(self.paths)['control']
        value = common.read_json(str(target))
        value['code_fingerprint'] = {'firstmate_revision': None}
        common.atomic_write_json(str(target), value)
        self.assertFalse(auto.view(self.paths)['auto_enabled'])
        self.assertIn('binding_format', auto.view(self.paths)['reason'])
        self.assertEqual(common.read_json(str(target)), value)

if __name__ == '__main__':
    unittest.main()
