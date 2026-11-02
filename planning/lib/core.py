"""Verify the exact restricted distribution, including its local helper adapter."""
import json
from common import ROOT, PlanningError, read_json, sha


def bundle():
    pin = read_json(ROOT / 'planning/pin.json')
    if not isinstance(pin, dict) or pin.get('version') != '1.2.4':
        raise PlanningError('Invalid core pin; restore this extension distribution before preparing projects')
    check = {k: v for k, v in pin.items() if k != 'digest'}
    if sha(json.dumps(check, sort_keys=True).encode()) != pin.get('digest'):
        raise PlanningError('Core pin digest mismatch; restore the pinned distribution')
    vendor = ROOT / 'vendor/spec-kit' / pin['version']
    data = (vendor / 'manifest.json').read_bytes()
    if sha(data) != pin['manifest_sha256']:
        raise PlanningError('Vendor manifest digest mismatch; restore the pinned distribution (no network fallback)')
    manifest = json.loads(data)
    if manifest['license'] != 'MIT' or manifest['commit'] != pin['commit']:
        raise PlanningError('Core provenance mismatch; restore the pinned distribution')
    assets = {}
    for name, record in manifest['files'].items():
        path = vendor / name
        if path.is_symlink() or sha(path.read_bytes()) != record['sha256']:
            raise PlanningError(f'Vendor digest mismatch: {name}; restore the pinned distribution')
        dest = ('.agents/' + name) if name.startswith('skills/') else ('.specify/' + name)
        if name == 'LICENSE':
            dest = '.specify/core/LICENSE'
        assets[dest] = (path.read_bytes(), 0o644)
    for name, digest in pin['helpers'].items():
        path = ROOT / 'planning/core' / name
        if path.is_symlink() or sha(path.read_bytes()) != digest:
            raise PlanningError('Helper digest mismatch; restore the pinned distribution')
        dest = '.specify/scripts/' + ('python/' if name.endswith('.py') else 'bash/') + name
        assets[dest] = (path.read_bytes(), 0o644 if name.endswith('.py') else 0o755)
    metadata = {'schema': 1, 'version': pin['version'], 'digest': pin['digest'],
                'harness': 'codex/claude-project-skills', 'capabilities': ['static-core', 'no-automation']}
    assets['.specify/core/integration.json'] = ((json.dumps(metadata, indent=2) + '\n').encode(), 0o644)
    # Claude Code discovers project skills in .claude/skills; Codex uses .agents/skills.
    for name, value in list(assets.items()):
        if name.startswith('.agents/skills/'):
            assets[name.replace('.agents/', '.claude/', 1)] = value
    return pin, assets
