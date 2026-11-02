"""Resolve once for a durable brief; availability never silently changes policy."""
import re
from common import MODES, PlanningError, VERSION, read_json
from config import layers
from core import bundle
from materialize import verify
from policy import enforce, inspect
from project_registry import identify
from preparation_records import directory, receipt


def brief_mode(path):
    text = path.read_text()
    if len(re.findall(r'^planning_mode:', text, re.M)) != 1:
        raise PlanningError('Brief must contain exactly one planning_mode declaration; repair the durable brief')
    matches = re.findall(r'^planning_mode:[ \t]*(\S+)[ \t]*$', text, re.M)
    if len(matches) != 1 or matches[0] not in MODES:
        raise PlanningError('Brief must contain exactly one planning_mode: native|speckit; repair the durable brief')
    return matches[0], text


def resolve(home, project, registry, task=None, brief=None, require_core=False, task_id=None):
    key, root = identify(project, registry)
    values, language = layers(home, key)
    if task_id:
        from task_preferences import mode_for
        selected = mode_for(home, root, task_id)
        if selected != 'inherit':
            values.insert(0, ('task', selected))
    frozen_text = ''
    if brief:
        frozen, frozen_text = brief_mode(brief)
        if task and frozen != task:
            raise PlanningError('Task override conflicts with the durable brief; preserve its mode on resume')
        task = frozen
    if task:
        values.insert(0, ('task', task))
    origin, mode = next((s, v) for s, v in values if v != 'inherit')
    pol = inspect(root)
    enforce(pol, mode)
    availability = 'not-required'
    core = None
    effective = mode
    cause = None
    if mode == 'speckit':
        pin, _ = bundle()
        core = {'version': pin['version'], 'digest': pin['digest']}
        for name, wanted in (('planning_core_version', core['version']), ('planning_core_digest', core['digest'])):
            existing = re.findall(rf'^{name}:[ \t]*(\S+)[ \t]*$', frozen_text, re.M)
            declarations = re.findall(rf'^{name}:', frozen_text, re.M)
            if frozen_text and (len(declarations) != 1 or len(existing) != 1):
                raise PlanningError(f'Brief requires exactly one {name}; restore its frozen pin before resuming')
            if existing and existing[0] != wanted:
                raise PlanningError('Brief core pin differs; retain its original distribution and environment, never upgrade an active task')
        try:
            evidence = verify(root, key, probe=False)
            availability = evidence['state']
        except PlanningError as exc:
            availability = 'pending'
            cause = str(exc)
            if key and (receipt(key) or read_json(directory(key) / 'failure.json')):
                availability = 'failed'
            effective = None
            if require_core:
                raise PlanningError(f'{availability}: {cause}; stop this dependent cycle and prepare/verify the project') from exc
    if frozen_text:
        for name in ('planning_origin', 'planning_resolver', 'planning_language'):
            entries = re.findall(rf'^{name}:[ \t]*(\S+)[ \t]*$', frozen_text, re.M)
            if len(re.findall(rf'^{name}:', frozen_text, re.M)) > 1 or (re.search(rf'^{name}:', frozen_text, re.M) and not entries):
                raise PlanningError(f'Invalid {name} in brief; repair its frozen provenance')
            if entries and name == 'planning_origin':
                origin = entries[0]
            if entries and name == 'planning_language':
                if entries[0] not in ('en', 'es'):
                    raise PlanningError('Invalid frozen language; repair the brief')
                language = entries[0]
            if entries and name == 'planning_resolver' and entries[0] != VERSION:
                raise PlanningError('Brief resolver version differs; use its original distribution on resume')
    lines = [f'planning_mode: {mode}', f'planning_origin: {origin}', f'planning_resolver: {VERSION}',
             f'planning_language: {language}']
    if core:
        lines += [f"planning_core_version: {core['version']}", f"planning_core_digest: {core['digest']}"]
    return {'mode': mode, 'effective': effective, 'origin': origin, 'resolver': VERSION,
            'project_id': key, 'project': str(root), 'core': core, 'availability': availability,
            'cause': cause, 'language': language, 'layers': [{'origin': k, 'mode': v} for k, v in values],
            'policy': pol, 'brief_lines': '\n'.join(lines), 'applies_to': 'new tasks; durable briefs remain frozen'}
