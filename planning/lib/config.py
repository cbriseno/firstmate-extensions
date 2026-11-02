"""Strict, private scoped preferences. Changes apply to future task resolution."""
from common import MODES, PlanningError, atomic, home_state, installation, read_json, regular


def load(directory):
    path = directory / 'config.json'
    obj = read_json(path, {'schema': 1, 'default': 'inherit', 'projects': {}, 'language': 'en'})
    valid = (isinstance(obj, dict) and set(obj) <= {'schema', 'default', 'projects', 'language'}
             and type(obj.get('schema')) is int and obj.get('schema') == 1 and obj.get('default', 'inherit') in (*MODES, 'inherit')
             and obj.get('language', 'en') in ('en', 'es') and isinstance(obj.get('projects', {}), dict))
    if not valid or any(not isinstance(k, str) or v not in (*MODES, 'inherit')
                        for k, v in obj.get('projects', {}).items()):
        raise PlanningError(f'Invalid planning config: {path}; repair schema/default/projects/language and retry')
    return {'schema': 1, 'default': 'inherit', 'projects': {}, 'language': 'en', **obj}


def home_config(home):
    obj = load(home_state(home))
    # Read the previous one-line preference only until the home has a new config.
    old = home / 'config' / 'planning-mode'
    if not (home_state(home) / 'config.json').exists() and old.exists():
        regular(old)
        value = old.read_text()
        if value not in ('native\n', 'speckit\n'):
            raise PlanningError(f'Invalid legacy config: {old}; write native or speckit followed by a newline')
        obj['default'] = value.strip()
        obj['legacy'] = True
    return obj


def save(directory, obj):
    atomic(directory / 'config.json', {k: v for k, v in obj.items() if k != 'legacy'})


def layers(home, project_id=None):
    inst = load(installation())
    local = home_config(home)
    values = []
    if project_id:
        values += [('project:home', local['projects'].get(project_id, 'inherit')),
                   ('project:installation', inst['projects'].get(project_id, 'inherit'))]
    values += [('home:legacy' if local.get('legacy') else 'home', local['default']),
               ('installation', inst['default']), ('built-in', 'native')]
    return values, local['language'] if (home_state(home) / 'config.json').exists() else inst['language']
