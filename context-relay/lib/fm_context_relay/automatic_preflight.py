"""Read-only launch proof before the irreversible outgoing retirement.

Proof is repeated by the shared assisted launch path after native exit; an
earlier successful preflight never authorizes skipping those final checks.
"""

import os
from pathlib import Path
import shutil

from . import assisted, automatic, common, pane_close

require = pane_close.require


def verify(args, paths, directory, attempt):
    home = Path(paths.home)
    automatic.authority(paths)  # the kill switch is always the first check
    package = assisted.validate_package(directory, attempt)
    assisted.confirmation(directory, attempt)
    require(attempt['state'] == 'confirmed', 'preflight out of phase')
    backend = pane_close.Backend(args)
    require(backend.incarnation() == package['backend'], 'backend incarnation changed')
    auth = assisted.load(directory/'authorization.json')
    owner = assisted.owner_for(home, attempt, backend, True)
    require(assisted.digest(paths.config) == package['config_sha256'], 'configuration changed')
    version = assisted.versions(home, auth['harness'], backend)
    require(version['harness'] == automatic.SUPPORTED_HARNESSES.get(auth['harness']),
            'harness version not verified for auto')
    for component in ('client', 'server'):
        observed = version['herdr'].get(component) or {}
        require(all(observed.get(k) == v for k, v in automatic.SUPPORTED_HERDR.items()),
                'Herdr version/protocol not verified for auto: '+component)
    require(assisted.version_key(version) == package['version_key'],
            'versiones cambiaron')
    original = assisted.argv_of(owner['process']['pid'])
    require(original == package['native_argv'], 'native argv changed')
    require(assisted.processes.executable_digest(owner['process']['pid']) == package['executable_sha256'],
            'native binary changed')
    assisted.no_resume(original)
    launch = assisted.load(directory/'launch.json')
    assisted.no_resume(launch['argv'])
    require(launch['argv'][-len(original):] == original, 'launcher does not preserve native argv')
    program = shutil.which(launch['argv'][0], path=launch['env'].get('PATH', os.defpath))
    require(program and Path(program).is_file() and os.access(program, os.X_OK),
            'launcher ausente/no ejecutable')
    assisted.peers(home, auth['harness'], [owner['process']['pid']])
    target = backend.terminal(auth['new_pane'])
    source = backend.terminal(auth['old_pane'])
    require(target == package['destination'] and source['tab'] == target['tab']
            and source['pane'] != target['pane'], 'sibling pane changed')
    pane_close.empty_shell(backend, target)
    require(shutil.disk_usage(home).free >= 3_000_000_000, 'less than 3 GB free before retirement')
    record = assisted.load(directory/'policy-request.json')
    latest = assisted.load(Path(paths.sessions)/(auth['harness']+'-'+auth['old_sid']+'.json'))
    require(latest.get('last_user_prompt_nonce') == record.get('last_user_prompt_nonce'),
            'new operator input; safe point must be prepared again')
    notes = assisted.load(directory/'notes.json')
    current = assisted.capture(home, notes['references'])
    authority = automatic.safe_point(paths, record, notes, current)
    proof = dict(schema='fm-auto-preflight.v1', at=common.iso(), attempt=attempt['id'],
                 checkpoint_sha256=attempt['checkpoint_sha256'], owner=owner,
                 authorization=authority['id'], destination=target,
                 operational=current, launcher=program)
    automatic.write(directory/'automatic-preflight.json', proof)
    assisted.event(directory, 'automatic-preflight', checkpoint_sha256=attempt['checkpoint_sha256'])
    return proof
