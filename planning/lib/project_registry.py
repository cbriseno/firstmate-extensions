"""Assigned private IDs bind canonical roots to filesystem identity, never remotes."""
from pathlib import Path
import uuid
from common import PlanningError, atomic, installation, read_json


def registry():
    path = installation() / 'registry.json'
    obj = read_json(path, {'schema': 1, 'projects': {}, 'homes': []})
    if (not isinstance(obj, dict) or set(obj) != {'schema', 'projects', 'homes'}
            or obj['schema'] != 1 or not isinstance(obj['projects'], dict)
            or not isinstance(obj['homes'], list) or any(not isinstance(h, str) for h in obj['homes'])):
        raise PlanningError(f'Invalid registry: {path}; restore its private backup and retry')
    for key, entry in obj['projects'].items():
        try:
            if str(uuid.UUID(key)) != key:
                raise ValueError('noncanonical ID')
        except (ValueError, TypeError, AttributeError) as exc:
            raise PlanningError('Invalid private project ID; restore the registry') from exc
        if (not isinstance(entry, dict) or set(entry) != {'root', 'identity'}
                or not isinstance(entry['root'], str) or not Path(entry['root']).is_absolute()
                or not isinstance(entry['identity'], list) or len(entry['identity']) != 2
                or any(type(n) is not int for n in entry['identity'])):
            raise PlanningError(f'Invalid registry entry {key}; restore the private registry and retry')
    return obj


def write(obj):
    atomic(installation() / 'registry.json', obj)


def root(value):
    if not value:
        raise PlanningError('Missing project; pass --project with its exact root')
    try:
        path = Path(value).resolve(strict=True)
    except OSError as exc:
        raise PlanningError(f'Project missing: {value}; restore it or explicitly relocate its private ID') from exc
    if not path.is_dir():
        raise PlanningError('Project must be a directory; pass its exact root')
    return path


def fingerprint(path):
    stat = path.stat()
    return [stat.st_dev, stat.st_ino]


def identify(value, obj, create=False):
    path = root(value)
    for key, entry in obj['projects'].items():
        if entry['root'] == str(path):
            if entry['identity'] != fingerprint(path):
                raise PlanningError('Registered root was replaced; verify the project identity before registering it anew')
            return key, path
    if create:
        key = str(uuid.uuid4())
        obj['projects'][key] = {'root': str(path), 'identity': fingerprint(path)}
        return key, path
    return None, path


def relocate(obj, key, target):
    if key not in obj['projects']:
        raise PlanningError('Unknown project ID; inspect planning status before relocating')
    path = root(target)
    if fingerprint(path) != obj['projects'][key]['identity']:
        raise PlanningError('Relocation identity differs; a clone or replacement is a different project')
    if any(k != key and e['root'] == str(path) for k, e in obj['projects'].items()):
        raise PlanningError('Relocation target already belongs to another private project ID')
    obj['projects'][key]['root'] = str(path)
