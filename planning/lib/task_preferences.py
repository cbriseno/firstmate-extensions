"""Private task preferences, bound to a home and canonical project identity."""
import re
from common import MODES, PlanningError, atomic, home_state, read_json
from project_registry import fingerprint


def path_for(home):
    return home_state(home) / 'tasks.json'


def task_id(value):
    if not isinstance(value, str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,127}', value):
        raise PlanningError('Task ID must contain 1..128 letters, digits, dots, underscores or hyphens')
    return value


def load(home):
    obj = read_json(path_for(home), {'schema': 1, 'tasks': {}})
    if not isinstance(obj, dict) or set(obj) != {'schema', 'tasks'} or obj['schema'] != 1 or not isinstance(obj['tasks'], dict):
        raise PlanningError('Invalid task planning preferences; repair tasks.json')
    for key, row in obj['tasks'].items():
        task_id(key)
        if (not isinstance(row, dict) or set(row) != {'project', 'identity', 'mode'}
                or not isinstance(row['project'], str) or not isinstance(row['identity'], list)
                or len(row['identity']) != 2 or any(type(n) is not int for n in row['identity'])
                or row['mode'] not in (*MODES, 'inherit')):
            raise PlanningError('Invalid task planning preference; repair tasks.json')
    return obj


def mode_for(home, project, selected):
    row = load(home)['tasks'].get(task_id(selected))
    if row and (row['project'] != str(project) or row['identity'] != fingerprint(project)):
        raise PlanningError('Task ID belongs to a different or replaced project')
    return row['mode'] if row else 'inherit'


def set_mode(home, project, selected, mode, dry_run=False):
    mode_for(home, project, selected)
    obj = load(home)
    obj['tasks'][selected] = dict(project=str(project), identity=fingerprint(project), mode=mode)
    if not dry_run:
        atomic(path_for(home), obj)
    return dict(scope='task', task_id=selected, home=str(home), project=str(project), mode=mode,
                dry_run=dry_run, applies_to='new task resolution; durable briefs remain frozen')
