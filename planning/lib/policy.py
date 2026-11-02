"""Surface project policies. Structured declarations are enforceable restrictions."""
import re
from common import PlanningError, sha


def inspect(root):
    files = []
    restrictions = set()
    for name in ('AGENTS.md', 'CLAUDE.md', '.specify/memory/constitution.md'):
        path = root / name
        if path.is_symlink():
            raise PlanningError(f'Policy file is a symlink: {name}; review its actual location before preparation')
        if path.exists():
            content = path.read_text()
            files.append({'path': name, 'sha256': sha(path.read_bytes()), 'text': content})
            modes = re.findall(r'^\s*(?:planning_policy|planning_mode):\s*(\S+)\s*$', content, re.M)
            if any(mode not in ('native', 'speckit', 'inherit') for mode in modes):
                raise PlanningError(f'Invalid planning policy in {name}; repair its declaration before resolving tasks')
            restrictions.update(mode for mode in modes if mode != 'inherit')
            if re.search(r'(?:do not|never|must not) (?:use|install|enable) spec kit', content, re.I):
                restrictions.add('native')
    if len(restrictions) > 1:
        raise PlanningError('Project policies conflict; reconcile AGENTS/CLAUDE/constitution before this cycle')
    digest = sha('\n'.join(f"{f['path']}:{f['sha256']}" for f in files).encode())
    return {'files': files, 'digest': digest, 'restriction': next(iter(restrictions), None),
            'review_required': bool(files)}


def enforce(policy, mode):
    if policy['restriction'] and policy['restriction'] != mode:
        raise PlanningError(f"Project policy requires {policy['restriction']}; change the requested scope or obtain a policy change from its owner")
