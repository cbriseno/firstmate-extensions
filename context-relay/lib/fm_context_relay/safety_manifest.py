"""Single authority-binding inventory. See README authorization binding.

Bump PROTOCOL_VERSION for every change to automatic transfer behavior.
New Python modules must be classified explicitly; coverage fails closed.
"""

from pathlib import Path
import subprocess

from . import common

PROTOCOL_VERSION = 3
EXTENSION_FILES = (
    'lib/fm_context_relay_main.py',
    'lib/fm_context_relay/__init__.py',
    *('lib/fm_context_relay/%s.py' % name for name in (
        'safety_manifest', 'common', 'identity', 'admission', 'measure',
        'session', 'hook', 'config', 'thresholds', 'automatic',
        'automatic_preflight', 'assisted', 'start', 'launch_once',
        'pane_close', 'herdr_session', 'checkpoint', 'events', 'relay_notifications', 'cli',
        'activation', 'install', 'intent', 'settings_preflight', 'process_inspection')),
    *('bin/fm-context-relay'+suffix for suffix in ('', '-hook', '-guard', '-statusline', '-shell')),
    'templates/successor-pane-close.txt',
)
CORE_FILES = tuple('bin/'+name for name in (
    'fm-session-start.sh', 'fm-sessionstart-run.sh', 'fm-lock.sh',
    'fm-lock-release.sh', 'fm-lock-release.py', 'fm-session-lock-lib.sh',
    'fm-primary-scope-lib.sh',
))


def uncovered(root):
    """Unclassified executable extension files, including future modules."""
    classified = set(EXTENSION_FILES)
    candidates = set(str(p.relative_to(root)) for p in (root/'lib').rglob('*.py'))
    candidates.update(str(p.relative_to(root)) for p in (root/'bin').iterdir() if p.is_file())
    return sorted(candidates-classified)


def fingerprint(paths, root=None):
    root = Path(root) if root else Path(__file__).resolve().parents[2]
    unknown = uncovered(root)
    if unknown:
        raise ValueError('safety manifest: unclassified executable files: '+', '.join(unknown))
    components = {}
    for prefix, base, names in (('extension', root, EXTENSION_FILES),
                                ('firstmate', Path(paths.home), CORE_FILES)):
        for name in names:
            path = base/name
            components[prefix+'/'+name] = common.sha256_bytes(path.read_bytes()) if path.is_file() else None
    head = subprocess.run(['git', '-C', paths.home, 'rev-parse', 'HEAD'],
                          capture_output=True, text=True, timeout=3)
    payload = dict(protocol_version=PROTOCOL_VERSION, components=components,
                   firstmate_revision=head.stdout.strip() if head.returncode == 0 else None)
    return dict(payload, manifest_hash=common.sha256_bytes(common.dump_json(payload)))


def changed(previous, current):
    if not isinstance(previous, dict) or 'manifest_hash' not in previous:
        return ['binding_format (legacy authorization)']
    parts = []
    if previous.get('protocol_version') != current['protocol_version']:
        parts.append('protocol_version')
    if previous.get('firstmate_revision') != current.get('firstmate_revision'):
        parts.append('firstmate_revision')
    old, new = previous.get('components') or {}, current['components']
    parts.extend(k for k in sorted(set(old) | set(new)) if old.get(k) != new.get(k) or (k in old) != (k in new))
    if previous.get('manifest_hash') != current['manifest_hash'] and not parts:
        parts.append('manifest_hash')
    return parts
