"""File ownership evidence lives outside the project, one receipt per private ID."""
import re
from common import PlanningError, installation, read_json


def directory(key):
    return installation() / 'projects' / key


def receipt(key):
    obj = read_json(directory(key) / 'receipt.json') if key else None
    if obj is not None and (not isinstance(obj, dict) or obj.get('schema') != 1
                            or not isinstance(obj.get('files'), dict)
                            or obj.get('state') not in ('prepared', 'verified')
                            or not isinstance(obj.get('digest'), str)
                            or not re.fullmatch('[a-f0-9]{64}', obj['digest'])
                            or not isinstance(obj.get('root'), str)
                            or obj.get('version') != '1.2.4'
                            or any(not isinstance(v, dict) or set(v) != {'sha256', 'mode'}
                                   or not isinstance(v['sha256'], str) or not re.fullmatch('[a-f0-9]{64}', v['sha256'])
                                   or type(v['mode']) is not int or v['mode'] not in (0o644, 0o755)
                                   for v in obj['files'].values())):
        raise PlanningError('Invalid preparation receipt; restore the private receipt or inspect the journal before retrying')
    return obj
