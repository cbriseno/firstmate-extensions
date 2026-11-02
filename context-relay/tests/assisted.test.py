"""Token-free public-CLI refusal checks, separate from native takeover evidence."""

import json
import os
import shlex
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest import mock
import sys
from types import SimpleNamespace

EXT = Path(__file__).resolve().parents[1]
sys.path.insert(0,str(EXT/'lib'))
from fm_context_relay import assisted


class AssistedCLI(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='fm-assisted-test-')
        self.home = Path(self.temp.name)/'home with spaces'
        (self.home/'state/context-relay').mkdir(parents=True)
        (self.home/'config').mkdir()
        self.env = dict(os.environ, HERDR_ENV='1')

    def tearDown(self):
        self.temp.cleanup()

    def cli(self, *args, payload=None):
        p = subprocess.run([str(EXT/'bin/fm-context-relay'),'--home',str(self.home),*args],
                           input=json.dumps(payload) if payload is not None else '',
                           capture_output=True,text=True,env=self.env,timeout=10)
        return p

    def test_help_names_explicit_confirmation_and_native_authority(self):
        p = self.cli('relay','--help')
        self.assertEqual(p.returncode,0)
        self.assertIn('confirm',p.stdout)
        self.assertIn('successor-ready',p.stdout)

    def test_warn_cannot_prepare_or_launch(self):
        p = self.cli('relay','prepare','--harness','codex','--notes','/absent','--launch','/absent',
                     '--new-pane','w1:p2','--authority','unit test','--herdr-session','fm-lab-test')
        self.assertEqual(p.returncode,1)
        self.assertIn('relay_mode is not assisted',p.stdout)
        self.assertFalse((self.home/'state/context-relay/active.json').exists())
        log=self.home/'data/context-relay/attempt-events.jsonl'
        self.assertEqual(json.loads(log.read_text())['kind'],'refused')

    def test_single_entry_in_warn_refuses_before_any_pane_or_attempt(self):
        p = self.cli('relay', 'start', '--harness', 'claude', '--herdr-session', 'fm-lab-test')
        self.assertEqual(p.returncode, 1)
        self.assertIn('relay_mode is not assisted', p.stdout)
        self.assertFalse((self.home/'state/context-relay/active.json').exists())

    def test_single_entry_cannot_confirm_without_prepared_package(self):
        p = self.cli('relay', 'start', '--confirm', 'invented', '--confirmation', 'Captain quote after summary')
        self.assertEqual(p.returncode, 1)
        self.assertIn('no prepared package', p.stdout)
        self.assertFalse((self.home/'state/context-relay/active.json').exists())

    def test_single_entry_abort_and_confirm_are_contradictory(self):
        p = self.cli('relay', 'start', '--abort', '--confirm', 'invented')
        self.assertEqual(p.returncode, 1)
        self.assertIn('--abort cannot be combined', p.stdout)

    def test_single_entry_help_explains_chat_confirmation_and_no_retries(self):
        p = self.cli('relay', 'start', '--help')
        self.assertEqual(p.returncode, 0)
        self.assertIn('relay request', p.stdout)
        self.assertIn('ONCE', p.stdout)
        self.assertIn('do not resend', p.stdout)

    def test_external_events_default_to_no_delivery(self):
        p = self.cli('show', '--json')
        self.assertEqual(p.returncode, 0)
        self.assertEqual(json.loads(p.stdout)['top']['relay_event_command']['value'], [])

    def test_external_events_reject_shell_strings_and_relative_executables(self):
        for value in ('"send something"', '["relative-consumer"]'):
            p = self.cli('set', 'relay_event_command', value, '--reason', 'fixture consumer', '--approved', 'fixture operator')
            self.assertEqual(p.returncode, 1)
            self.assertIn('relay_event_command must be', p.stdout)
        self.assertFalse((self.home/'config/context-relay.json').exists())

    def test_inactive_shell_hook_is_inert_even_for_unknown_payload(self):
        p=subprocess.run([str(EXT/'bin/fm-context-relay-guard'),'--home',str(self.home),'codex','PreToolUse'],
                         input='not-json',capture_output=True,text=True,timeout=10)
        self.assertEqual((p.returncode,p.stdout),(0,''))

    def test_broken_active_state_fails_open_and_logs_error(self):
        (self.home/'state/context-relay/active.json').write_text('{bad')
        p=self.cli('relay','guard',payload={'tool_name':'Bash','tool_input':{'command':'touch should-not-run'}})
        self.assertEqual((p.returncode,p.stdout,p.stderr),(0,'',''))
        self.assertTrue((self.home/'state/context-relay/hook-errors.log').exists())
        self.assertFalse((self.home/'should-not-run').exists())

    def test_foreign_attempt_pointer_is_not_followed_and_fails_open(self):
        (self.home/'state/context-relay/active.json').write_text(json.dumps({'directory':'/other-home/attempt'}))
        p=self.cli('relay','guard',payload={'tool_name':'Bash','tool_input':{'command':'true'}})
        self.assertEqual((p.returncode,p.stdout,p.stderr),(0,'',''))
        self.assertIn('unrelated path',(self.home/'state/context-relay/hook-errors.log').read_text())

    def test_resume_flag_is_not_an_accepted_launch_option(self):
        p=self.cli('relay','launch','--handoff',str(self.home/'data/context-relay/attempts/id'),
                   '--herdr-session','fm-lab-test','--resume','old-live-session')
        self.assertEqual(p.returncode,2)
        self.assertFalse((self.home/'state/context-relay/active.json').exists())

    def test_status_never_reads_another_home(self):
        p=self.cli('relay','status','--handoff','/other-home/attempt')
        self.assertEqual(p.returncode,1)
        self.assertIn('outside this home attempt directory',p.stdout)

    def test_shell_launcher_refuses_a_new_shell(self):
        p=subprocess.run(['bash',str(EXT/'bin/fm-context-relay-shell'),'--home',str(self.home),'--','true'],
                         capture_output=True,text=True,timeout=10)
        self.assertEqual(p.returncode,2)
        self.assertIn('must be sourced',p.stderr)

    def test_worker_of_other_harness_keeps_its_own_native_identity(self):
        directory=self.home/'data/context-relay/attempts/test'
        directory.mkdir(parents=True)
        (directory/'attempt.json').write_text(json.dumps(dict(id='test',home=str(self.home),state='prepared')))
        (directory/'authorization.json').write_text(json.dumps(dict(harness='codex')))
        (self.home/'state/context-relay/active.json').write_text(json.dumps(dict(directory=str(directory),attempt='test')))
        paths=SimpleNamespace(home=str(self.home),state_dir=str(self.home/'state/context-relay'))
        payload=dict(tool_name='Bash',tool_input=dict(command='write worker status'))
        with mock.patch.object(assisted,'native',return_value=('worker',123,None,self.home/'projects/worker',None,None)) as native:
            self.assertIsNone(assisted.guard(paths,payload,'claude'))
            native.assert_called_once_with(self.home,'claude',payload)

    def test_quoted_recovery_punctuation_does_not_authorize_shell_operators(self):
        directory=self.home/'data/context-relay/attempts/test'
        directory.mkdir(parents=True)
        (directory/'attempt.json').write_text(json.dumps(dict(id='test',home=str(self.home),state='prepared')))
        (directory/'authorization.json').write_text(json.dumps(dict(harness='claude')))
        (self.home/'state/context-relay/active.json').write_text(json.dumps(dict(directory=str(directory),attempt='test')))
        paths=SimpleNamespace(home=str(self.home),state_dir=str(self.home/'state/context-relay'))
        cli=str(EXT/'bin/fm-context-relay')
        command=shlex.join([cli,'--home',str(self.home),'relay','accept','--handoff',str(directory),
            '--review','Reviewed E1 and E2; worker inbox is pending (not acknowledged) & blockers remain.','--blockers','[]'])
        def verdict(text):
            return assisted.guard(paths,dict(tool_name='Bash',tool_input=dict(command=text)),'claude')
        with mock.patch.object(assisted,'native',return_value=('successor',123,None,self.home,None,None)):
            self.assertIsNone(verdict(command))
            for unsafe in (command+'; touch sentinel',command+' && touch sentinel',command+' | cat',
                           command+' > sentinel',command+'\n touch sentinel',command+' <(touch sentinel)',
                           command+' --review "$(touch sentinel)"',command+' --review "`touch sentinel`"'):
                with self.subTest(command=unsafe):self.assertIsNotNone(verdict(unsafe))


if __name__=='__main__':
    unittest.main()
