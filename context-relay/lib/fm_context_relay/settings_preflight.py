"""Read-only activation eligibility for settings clients; never drives a backend."""
import json
import os
from pathlib import Path
import sys

from . import automatic, common, config, install, pane_close


def view(paths, harness, codex_home=None):
    reasons = []
    authorization = []

    def add(code, message):
        reasons.append(dict(code=code, message=message))

    if sys.platform != 'linux':
        add('unsupported_platform', 'Automatic relay is verified on Linux only')
    if harness not in automatic.SUPPORTED_HARNESSES:
        add('unsupported_harness', 'Automatic relay supports claude and codex only')
    else:
        if harness == 'codex':
            directory = Path(codex_home or os.environ.get('CODEX_HOME') or Path.home()/'.codex')
            path = directory / 'hooks.json'
            events = install.CODEX_EVENTS
        else:
            path = Path(install._settings_path(paths.home))
            events = install.CLAUDE_EVENTS
        try:
            # This is deliberately a read, not installation or trust approval.
            data = path.read_bytes() if path.exists() else None
            obj = json.loads(data) if data is not None else {}
            hooks = obj.get('hooks', {})
            if not isinstance(hooks, dict):
                raise ValueError('hooks must be an object')
            missing = []
            for event, _ in events:
                groups = hooks.get(event, [])
                if not isinstance(groups, list):
                    raise ValueError('hook groups must be lists')
                expected = install.hook_command(paths.home, harness, event)
                if not any(isinstance(group, dict) and isinstance(group.get('hooks'), list)
                           and any(isinstance(h, dict) and h.get('type') == 'command'
                                   and h.get('command') == expected for h in group['hooks']) for group in groups):
                    missing.append(event)
            if missing:
                add('missing_hooks', 'Missing relay hooks: '+', '.join(missing))
        except (OSError, ValueError, TypeError, AttributeError) as exc:
            add('invalid_hooks', str(exc))
        if harness == 'codex':
            add('codex_trust_unverified', 'Prepared Codex hooks do not prove native /hooks trust and activation')
    loaded = config.load(paths)
    if loaded.errors:
        add('invalid_configuration', '; '.join(loaded.errors))
    if not automatic.locations(paths)['control'].exists():
        authorization.append(dict(code='missing_standing_authorization', message='No recorded standing authorization'))
    else:
        try:
            automatic.read_authority(paths)
        except (pane_close.Preserve, OSError, ValueError) as exc:
            authorization.append(dict(code='invalid_standing_authorization', message=str(exc)))
    return dict(schema='fm-extension-settings-preflight.v1', home=paths.home, harness=harness,
                eligible=not reasons and not authorization, can_authorize=not reasons,
                reasons=reasons+authorization, authorization_reasons=authorization)
