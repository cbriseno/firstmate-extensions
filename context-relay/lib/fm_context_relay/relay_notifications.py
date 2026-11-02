"""Generic, opt-in notification adapter and durable failure-only outbox.

The public extension never selects a service. The user's adapter owns any
external transport; an accepted event never proves delivery to a device.
"""

import json
import os
from pathlib import Path
import subprocess
import sys

from . import automatic, common, config, pane_close


def adapter(paths):
    loaded = config.load(paths)
    pane_close.require(loaded.status == 'ok', 'invalid configuration for notification arming')
    argv = loaded.settings.value('notification_adapter')
    if not argv:
        return dict(argv=[], sha256=None)
    path = Path(argv[0])
    pane_close.require(path.is_absolute() and path.resolve() == path and path.is_file()
                       and os.access(path, os.X_OK), 'adapter is noncanonical or not executable')
    return dict(argv=argv, sha256=common.sha256_bytes(path.read_bytes()))


def invoke(paths, selected, action, attempt, reason='', recovery=''):
    if not selected['argv']:
        return dict(ok=True, state='disabled')
    path = Path(selected['argv'][0])
    pane_close.require(path.resolve() == path and common.sha256_bytes(path.read_bytes()) == selected['sha256'],
                       'adapter changed; preserve durable alert')
    payload = dict(schema='fm-relay-notification.v1', action=action, home=paths.home,
                   attempt=attempt['id'], checkpoint_sha256=attempt['checkpoint_sha256'],
                   at=common.iso(), summary='Relay failed; recovery required' if action == 'failure' else '',
                   reason=reason, recovery=recovery)
    p = subprocess.run(selected['argv'], cwd=paths.home, input=json.dumps(payload),
                       capture_output=True, text=True, timeout=20)
    pane_close.require(p.returncode == 0, 'adapter rejected '+action+': '+p.stderr[-500:])
    value = json.loads(p.stdout)
    pane_close.require(isinstance(value, dict) and value.get('ok') is True,
                       'adapter has no structured acknowledgment')
    return value


def arm(paths, directory, attempt):
    target = directory/'notification-armed.json'
    with common.FileLock(str(directory/'.notification-outbox.lock')):
        previous = common.read_json(str(target))
        if previous:
            pane_close.require(previous['attempt'] == attempt['id']
                               and previous['checkpoint_sha256'] == attempt['checkpoint_sha256'], 'arming belongs to another package')
            pane_close.require(previous['state'] in ('armed', 'disabled'),
                               'previous arming failed or is uncertain; do not retire')
            return previous
        selected = adapter(paths)
        result = dict(at=common.iso(), state='arm-intent', attempt=attempt['id'],
                      checkpoint_sha256=attempt['checkpoint_sha256'], adapter=selected)
        automatic.write(target, result)
        value = invoke(paths, selected, 'arm', attempt)
        result.update(state='disabled' if not selected['argv'] else 'armed', result=value)
        automatic.write(target, result)
        return result


def disarm(paths, directory, attempt):
    target = directory/'notification-disarmed.json'
    with common.FileLock(str(directory/'.notification-outbox.lock')):
        previous = common.read_json(str(target))
        if previous:
            return previous
        armed = common.read_json(str(directory/'notification-armed.json'))
        pane_close.require(armed and armed['attempt'] == attempt['id']
                           and armed['checkpoint_sha256'] == attempt['checkpoint_sha256'], 'package arming missing')
        result = dict(at=common.iso(), state='disarm-intent')
        automatic.write(target, result)
        try:
            value = invoke(paths, armed['adapter'], 'disarm', attempt)
            result.update(state='disabled' if not armed['adapter']['argv'] else 'disarmed', result=value)
        except (OSError, ValueError, pane_close.Preserve, subprocess.SubprocessError) as exc:
            result.update(state='disarm-refused', error=str(exc))
        automatic.write(target, result)
        return result


def emit(paths, directory, attempt, reason, recovery):
    """Save intent before invoking. Ambiguous responses are never replayed."""
    target = directory/'notification-outbox.json'
    with common.FileLock(str(directory/'.notification-outbox.lock')):
        prior = common.read_json(str(target))
        if prior:
            prior.setdefault('subsequent_failures', []).append(dict(at=common.iso(), reason=reason, recovery=recovery))
            automatic.write(target, prior)
            return prior
        result = dict(schema='fm-relay-failure-outbox.v1', home=paths.home,
                      attempt=attempt['id'], checkpoint_sha256=attempt['checkpoint_sha256'],
                      at=common.iso(), reason=reason, recovery=recovery, state='pending',
                      adapter_accepted=False, delivery_confirmed=False)
        automatic.write(target, result)
        print('Relay stopped: '+reason+'. Recovery: '+recovery, file=sys.stderr, flush=True)
        try:
            armed = common.read_json(str(directory/'notification-armed.json'))
            pane_close.require(armed and armed['attempt'] == attempt['id']
                               and armed['checkpoint_sha256'] == attempt['checkpoint_sha256'], 'package arming missing')
            value = invoke(paths, armed['adapter'], 'failure', attempt, reason, recovery)
            result.update(state='pending-adapter-disabled' if not armed['adapter']['argv'] else 'adapter-accepted',
                          adapter_accepted=bool(armed['adapter']['argv']), result=value)
        except (OSError, ValueError, pane_close.Preserve, subprocess.SubprocessError) as exc:
            result.update(state='adapter-refused-or-uncertain', error=str(exc))
        automatic.write(target, result)
        automatic.journal(paths, 'failure-alert', **result)
        return result
