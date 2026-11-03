import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from types import SimpleNamespace
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'lib'))
from fm_context_relay import assisted, herdr_session, pane_close, start

FIXTURE = Path(__file__).parent / 'fixtures/herdr-status-default.json'


class SessionNormalizationTests(unittest.TestCase):
    def test_canonical_values_and_rejections(self):
        self.assertEqual(herdr_session.normalize('default'), 'default')
        self.assertEqual(herdr_session.normalize(None), 'default')
        self.assertEqual(herdr_session.normalize('named'), 'named')
        for invalid in ('', ' ', ' named', 'named ', 'na me', 3, [], {}):
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                herdr_session.normalize(invalid)

    def test_real_status_shape_default_null_and_named_exact_match(self):
        status = json.loads(FIXTURE.read_text())
        self.assertEqual(status['server']['version'], '0.9.0')
        self.assertEqual(status['client']['version'], '0.9.3')
        self.assertEqual(status['server']['protocol'], 22)
        backend = pane_close.Backend.__new__(pane_close.Backend)
        backend.session = 'default'
        backend.call = lambda *args: status
        with tempfile.TemporaryDirectory() as tmp:
            # Backend.incarnation verifies the reported endpoint is a socket.
            import socket
            endpoint = str(Path(tmp) / 'herdr.sock')
            sock = socket.socket(socket.AF_UNIX)
            sock.bind(endpoint)
            try:
                status['server']['socket'] = endpoint
                incarnation = pane_close.Backend.incarnation(backend)
                self.assertEqual(incarnation['session'], 'default')
                backend.session = 'named'
                with self.assertRaises(pane_close.Preserve):
                    pane_close.Backend.incarnation(backend)
                backend.session = 'default'
                status['server']['session'] = 'named'
                with self.assertRaises(pane_close.Preserve):
                    pane_close.Backend.incarnation(backend)
            finally:
                sock.close()

    def test_missing_herdr_session_requires_default_and_exact_socket_and_panel(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp).resolve()
            (home / 'state').mkdir()
            (home / 'state/.lock').write_text('123\n')
            (home / 'state/.session-start-complete').write_text('123\n')
            (home / 'state/.lock-session').write_text('native-sid\n')
            shell = dict(pid=456, start='shell-start', tty=5, session=7)
            backend = mock.Mock()
            backend.session = 'default'
            backend.socket = '/synthetic/herdr.sock'
            backend.incarnation.return_value = {'session': 'default', 'socket': backend.socket}
            backend.terminal.return_value = {'shell': shell}
            env = {'HERDR_PANE_ID': 'w1:p2', 'HERDR_SOCKET_PATH': backend.socket}
            owner_data = ('native-sid', 123, None, home, None, None)
            native = {'pid': 123, 'start': 'native-start', 'tty': 5, 'session': 7}
            with mock.patch.dict(os.environ, env, clear=True), \
                 mock.patch.object(pane_close.native_identity, 'native', return_value=owner_data), \
                 mock.patch.object(pane_close, 'identity', return_value=native), \
                 mock.patch.object(pane_close.subprocess, 'run', return_value=subprocess.CompletedProcess([], 0)):
                owner = start.identify(home, 'claude', backend)
                self.assertEqual(owner[0], 'claude')
                self.assertEqual(owner[1]['pane'], 'w1:p2')
                os.environ['HERDR_SESSION'] = 'other'
                with self.assertRaises(pane_close.Preserve):
                    pane_close.current_owner(home, 'claude', backend, 'w1:p2')
                del os.environ['HERDR_SESSION']
                os.environ['HERDR_SOCKET_PATH'] = '/synthetic/other.sock'
                with self.assertRaises(pane_close.Preserve):
                    pane_close.current_owner(home, 'claude', backend, 'w1:p2')
                os.environ['HERDR_SOCKET_PATH'] = backend.socket
                backend.terminal.side_effect = pane_close.Preserve('panel ausente')
                with self.assertRaises(pane_close.Preserve):
                    pane_close.current_owner(home, 'claude', backend, 'w1:p2')

    def test_named_session_requires_exact_server_match(self):
        for value, accepted in (('named', True), (None, False), ('default', False), ('other', False)):
            with self.subTest(server=value):
                backend = pane_close.Backend.__new__(pane_close.Backend)
                backend.session = 'named'
                backend.call = lambda *args, value=value: {'server': {
                    'running': True, 'session': value, 'compatible': True,
                    'socket': '/not-a-socket'}}
                with mock.patch('fm_context_relay.pane_close.os.stat', return_value=mock.Mock(st_mode=0o140000, st_dev=1, st_ino=2, st_ctime_ns=3)), \
                     mock.patch('fm_context_relay.pane_close.stat.S_ISSOCK', return_value=True), \
                     mock.patch('fm_context_relay.pane_close.Path.read_text', return_value='synthetic-boot'):
                    if accepted:
                        self.assertEqual(pane_close.Backend.incarnation(backend)['session'], 'named')
                    else:
                        with self.assertRaises(pane_close.Preserve):
                            pane_close.Backend.incarnation(backend)

    def test_prepare_flow_identifies_owner_and_writes_canonical_default(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp).resolve()
            state, config_dir, data = home / 'state', home / 'config', home / 'data'
            sessions = state / 'context-relay/sessions'
            for directory in (state, config_dir, data, sessions):
                directory.mkdir(parents=True, exist_ok=True)
            config_path = config_dir / 'context-relay.json'
            config_path.write_text('{}')
            (state / '.lock').write_text('123\n')
            (state / '.session-start-complete').write_text('123\n')
            (state / '.lock-session').write_text('native-sid\n')
            policy = {'trigger': 'synthetic'}
            record = {'harness': 'claude', 'session_id': 'native-sid', 'owner_pid': '123',
                      'relay': {'pending': True, 'policy': policy}}
            (sessions / 'claude-native-sid.json').write_text(json.dumps(record))
            notes_path = data / 'notes.json'
            notes_path.write_text(json.dumps({key: [] for key in assisted.NOTE_KEYS} | {
                'safe_point': 'Synthetic reconciled safe point.'}))
            launch_path = data / 'launch.json'
            launch_path.write_text(json.dumps({'argv': ['claude'], 'env': {}}))
            shell = {'pid': 456, 'start': 'shell-start', 'tty': 5, 'session': 7}
            old = {'pane': 'w1:p1', 'terminal': 't1', 'tab': 'tab1', 'workspace': 'ws', 'shell': shell}
            new = {'pane': 'w1:p2', 'terminal': 't2', 'tab': 'tab1', 'workspace': 'ws',
                   'shell': dict(shell, pid=457, tty=6)}
            backend = mock.Mock()
            backend.session = 'default'
            backend.socket = '/synthetic/herdr.sock'
            backend.incarnation.return_value = {'session': 'default', 'socket': backend.socket}
            backend.terminal.side_effect = lambda pane: old if pane == 'w1:p1' else new
            status = json.loads(FIXTURE.read_text())
            backend.call.return_value = status
            args = SimpleNamespace(harness='claude', herdr_session=None, herdr_lab_helper=None,
                                   notes=str(notes_path), launch=str(launch_path), new_pane='w1:p2',
                                   authority='synthetic owner authorization', park_after_exit=True)
            paths = SimpleNamespace(home=str(home), state_dir=str(state), config=str(config_path),
                                    data_dir=str(data), sessions=str(sessions))
            loaded = SimpleNamespace(settings=SimpleNamespace(value=lambda key: 'assisted'),
                                     status='ok', errors=[])
            if not hasattr(assisted, 'processes'):
                assisted.processes = SimpleNamespace(executable_digest=lambda pid: 'synthetic-sha256')
            env = {'HERDR_ENV': '1', 'HERDR_PANE_ID': 'w1:p1', 'HERDR_SOCKET_PATH': backend.socket}
            native = {'pid': 123, 'start': 'native-start', 'tty': 5, 'session': 7}
            with mock.patch.dict(os.environ, env, clear=True), \
                 mock.patch.object(assisted.config, 'load', return_value=loaded), \
                 mock.patch.object(assisted.session, 'relay_policy', return_value=policy), \
                 mock.patch.object(assisted.pane_close, 'Backend', return_value=backend), \
                 mock.patch.object(assisted.pane_close.native_identity, 'native',
                                   return_value=('native-sid', 123, None, home, None, None)), \
                 mock.patch.object(assisted.pane_close, 'identity', return_value=native), \
                 mock.patch.object(assisted.pane_close.subprocess, 'run',
                                   return_value=subprocess.CompletedProcess([], 0)), \
                 mock.patch.object(assisted, 'command', return_value='synthetic-firstmate-head'), \
                 mock.patch.object(assisted, 'digest', return_value='synthetic-sha256'), \
                 mock.patch.object(assisted.processes, 'executable_digest', return_value='synthetic-sha256'), \
                 mock.patch.object(assisted, 'peers'), \
                 mock.patch.object(assisted, 'argv_of', return_value=['claude']), \
                 mock.patch.object(assisted, 'harness_files', return_value={}), \
                 mock.patch.object(assisted, 'capture', return_value={'references': []}), \
                 mock.patch.object(assisted.pane_close, 'input_counters', return_value={'rchar': 0, 'syscr': 0}), \
                 mock.patch.object(assisted.pane_close, 'empty_shell'), \
                 mock.patch.object(assisted.pane_close, 'execute', side_effect=lambda *a: print('{"status":"prepared"}') or 0):
                assisted.prepare(args, paths)
            attempt = json.loads(next((home / 'data/context-relay/attempts').glob('*/attempt.json')).read_text())
            package = json.loads(next((home / 'data/context-relay/attempts').glob('*/package.json')).read_text())
            versions = json.loads(next((home / 'data/context-relay/attempts').glob('*/versions.json')).read_text())
            self.assertEqual(attempt['herdr_session'], 'default')
            self.assertEqual(package['backend']['session'], 'default')
            self.assertEqual(versions['herdr']['server']['session'], 'default')


if __name__ == '__main__':
    unittest.main()
