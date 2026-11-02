"""Human threshold syntax; translates into relay_policy, never decides eligibility."""

import copy
import json
import os
import re
from decimal import Decimal

from . import common

INITIAL = 'claude: compactaciones:2 umbral:80%\ncodex: compactaciones:4\n'


def path_for(paths):
    return os.path.join(paths.home, 'config', 'umbrales-relevo')


def error(problem):
    raise ValueError(problem + '. Ejemplo: umbral:600K o compactaciones:5')


def amount(text):
    text = text.lower()
    if text.endswith('%'):
        if not re.fullmatch(r'\d+(?:\.\d+)?%', text):
            error('porcentaje mal escrito')
        value = Decimal(text[:-1])
        if not 0 < value <= 100:
            error('percentage must be greater than 0 and at most 100%')
        return 'min_context_percent', float(value)
    unit = text[-1] if text[-1:] in ('k', 'm') else ''
    numeric = text[:-1] if unit else text
    if re.fullmatch(r'\d{1,3}([,._])\d{3}(?:\1\d{3})*', numeric):
        numeric = re.sub(r'[,._]', '', numeric)
    elif not re.fullmatch(r'\d+(?:\.\d+)?' if unit else r'\d+', numeric):
        error('malformed amount; separate thousands with period, comma or underscore')
    number = Decimal(numeric)
    number *= {'': 1, 'k': 1000, 'm': 1000000}[unit]
    if number <= 0 or number != number.to_integral_value():
        error('threshold must be a positive integer token count')
    return 'min_context_tokens', int(number)


def parse_spec(text):
    text = re.sub(r'(umbral:\d{1,3})([ \u00a0]\d{3})+(?=[kKmM]?(?:\s|$))', lambda m: m[0].replace(' ', '_').replace('\u00a0', '_'), text, flags=re.IGNORECASE)
    words = text.lower().strip().split()
    if not words:
        error('specification missing')
    policy = {}
    connector = None
    for i, word in enumerate(words):
        if word in ('o', 'or', 'y', 'and'):
            if connector is not None or i != 1 or len(words) != 3:
                error('repeated or misplaced connector')
            connector = 'or' if word in ('o', 'or') else 'and'
            continue
        if word.startswith('umbral:'):
            if any(k in policy for k in ('min_context_percent', 'min_context_tokens')):
                error('repeated or conflicting threshold')
            key, value = amount(word[len('umbral:'):])
        elif word.startswith('compactaciones:'):
            value = word[len('compactaciones:'):]
            if not re.fullmatch(r'[0-9]+', value) or int(value) < 1:
                error('compactions must be a positive integer')
            key, value = 'compactions', int(value)
            if key in policy:
                error('compactaciones repetidas')
        else:
            error('token unknown: ' + word)
        policy[key] = value
    if connector:
        if len(policy) != 2:
            error('connector requires threshold and compactions')
        if connector == 'or':
            policy['operator'] = 'or'
    return policy


def parse_lines(text):
    entries = {}
    for number, line in enumerate(text.splitlines(), 1):
        line = line.strip()
        if not line or line.startswith('#'):
            continue
        harness = 'default'
        match = re.match(r'^([a-zA-Z]+):\s*(.*)$', line)
        if match and match[1].lower() not in ('umbral', 'compactaciones'):
            harness, line = match[1].lower(), match[2]
            if harness not in ('claude', 'codex'):
                error('unknown harness at line %d: %s' % (number, harness))
        if harness in entries:
            error('repeated line for ' + harness)
        try:
            entries[harness] = parse_spec(line)
        except ValueError as exc:
            raise ValueError('line %d: %s' % (number, exc)) from exc
    if not entries:
        error('file has no thresholds')
    return entries


def format_spec(policy):
    parts = []
    if 'compactions' in policy:
        parts.append('compactaciones:%s' % policy['compactions'])
    if 'min_context_percent' in policy:
        parts.append('umbral:%g%%' % policy['min_context_percent'])
    if 'min_context_tokens' in policy:
        parts.append('umbral:%d' % policy['min_context_tokens'])
    return (' o ' if policy.get('operator') == 'or' else ' ').join(parts)


def initial_for(settings):
    policies = settings.value('relay_policy')
    if not policies:
        return INITIAL
    return ''.join('%s: %s\n' % (h, format_spec(p)) for h, p in policies.items())


def resolve(entries, legacy):
    policies, sources = {}, {}
    for harness in ('claude', 'codex'):
        key = harness if harness in entries else 'default' if 'default' in entries else None
        if key:
            policies[harness] = entries[key]
            sources[harness] = 'umbrales-relevo: ' + key
        elif harness in legacy:
            policies[harness] = legacy[harness]
            sources[harness] = 'context-relay.json: relay_policy.' + harness
        else:
            sources[harness] = 'no policy'
    return policies, sources


def overlay(paths, loaded, remember):
    from . import config
    path = path_for(paths)
    data = common.read_bytes(path)
    loaded.thresholds = {'path': path, 'present': data is not None, 'errors': [], 'entries': {}}
    if data is None and not os.path.lexists(path):
        return loaded
    cache_path = os.path.join(paths.state_dir, 'last-valid-umbrales.json')
    try:
        if data is None:
            error('cannot read ' + path)
        entries = parse_lines(data.decode('utf-8'))
    except (ValueError, UnicodeError) as exc:
        message = 'umbrales-relevo: ' + str(exc)
        loaded.thresholds['errors'] = [message]
        loaded.status = 'invalid'
        loaded.errors.append(message)
        loaded.sha = common.sha256_bytes(data or b'unreadable-thresholds')
        cached = common.read_json(cache_path)
        entries = (cached or {}).get('entries', {})
        loaded.using = 'last-valid' if entries else loaded.using
        loaded.thresholds['fallback'] = 'last valid translation' if entries else 'relay_policy JSON vigente'
    else:
        if remember:
            cached = common.read_json(cache_path) or {}
            if cached.get('entries') != entries:
                common.atomic_write_json(cache_path, {'entries': entries})
    loaded.thresholds['entries'] = entries
    policies, sources = resolve(entries, loaded.settings.value('relay_policy'))
    raw = copy.deepcopy(loaded.settings.raw)
    raw['relay_policy'] = policies
    loaded.settings = config.Settings(raw, loaded.settings.origin)
    loaded.thresholds['sources'] = sources
    return loaded


def machine_view(paths):
    from . import config
    loaded = config.load(paths)
    policies, sources = resolve(loaded.thresholds['entries'], loaded.settings.value('relay_policy'))
    return dict(schema='fm-relay-thresholds.v1', home=paths.home, path=path_for(paths),
                status=loaded.status, mode=loaded.settings.value('relay_mode'),
                policies=policies, sources=sources, thresholds=loaded.thresholds,
                errors=loaded.errors, warnings=loaded.warnings)


def run(args, paths):
    from . import config
    path = path_for(paths)
    try:
        if args.action == 'show':
            if getattr(args, 'json', False):
                value = machine_view(paths)
                print(json.dumps(value, ensure_ascii=False))
                return 1 if value['errors'] else 0
            loaded = config.load(paths)
            print('Archivo: ' + path)
            print('Mode: ' + loaded.settings.value('relay_mode'))
            for problem in loaded.errors:
                print('error: ' + problem)
            entries = loaded.thresholds['entries']
            policies, sources = resolve(entries, loaded.settings.value('relay_policy'))
            for harness, source in sources.items():
                if loaded.thresholds.get('fallback'):
                    source += '; respaldo: ' + loaded.thresholds['fallback']
                policy = policies.get(harness)
                print('%s: %s [%s]' % (harness, format_spec(policy) if policy else 'no policy', source))
                print('  relay_policy: ' + json.dumps(policy, ensure_ascii=False))
                if policy and 'min_context_percent' in policy:
                    print('  Window: observed by harness; fallback to model window_tokens. Neither available: window_unknown; never guess.')
            from .cli import _sessions
            for record in _sessions(paths, 5):
                harness = record.get('harness')
                if harness not in policies:
                    continue
                observed = record.get('window_tokens_observed')
                configured = loaded.settings.for_model(record.get('model'))['window_tokens']
                print('  Session %s (%s): window %s [%s]' % (record.get('session_id'), harness,
                    observed or configured or 'unresolved', 'observed' if observed else 'configured' if configured else 'window_unknown'))
            if loaded.thresholds['errors']:
                print('Using %s; repair the file or use reset.' % loaded.thresholds['fallback'])
            return 1 if loaded.errors else 0
        if args.action == 'check':
            entries = parse_lines(args.spec)
            if getattr(args, 'json', False):
                print(json.dumps(dict(schema='fm-relay-threshold-check.v1', valid=True, entries=entries, written=False), ensure_ascii=False))
                return 0
            print(json.dumps(entries, ensure_ascii=False, sort_keys=True))
            print('Valid; no writes.')
            return 0
        if os.path.realpath(path) != path:
            error('threshold file contains a symlink')
        with common.FileLock(paths.config_lock, timeout=5):
            before = common.read_bytes(path)
            if args.action == 'reset':
                text = INITIAL
            else:
                updates = parse_lines(args.spec)
                if len(updates) != 1:
                    error('set accepts a single line')
                key, policy = next(iter(updates.items()))
                if args.harness:
                    if key != 'default' and key != args.harness:
                        error('prefix conflicts with --harness')
                    key = args.harness
                entries = parse_lines(before.decode('utf-8')) if before is not None else parse_lines(initial_for(config._load(paths).settings))
                entries[key] = policy
                text = ''.join(('' if h == 'default' else h + ': ') + format_spec(p) + '\n' for h, p in entries.items())
            common.atomic_write_bytes(path, text.encode('utf-8'))
            common.append_line(paths.changes_log, json.dumps({'at': common.iso(), 'kind': 'umbral-' + args.action,
                'sha256_before': common.sha256_bytes(before) if before is not None else None,
                'sha256_after': common.sha256_bytes(text.encode('utf-8'))}, ensure_ascii=False))
        if getattr(args, 'json', False):
            print(json.dumps(dict(machine_view(paths), operation=args.action, written=True), ensure_ascii=False))
        else:
            print('Escrito: ' + path)
        return 0
    except (ValueError, UnicodeError) as exc:
        if getattr(args, 'json', False):
            print(json.dumps(dict(schema='fm-relay-threshold-error.v1', status='refused', reason=str(exc)), ensure_ascii=False))
        else:
            print('error: ' + str(exc))
        return 2


def add_parser(sub):
    p = sub.add_parser('umbral', help='umbrales sencillos, editables en caliente')
    actions = p.add_subparsers(dest='action', required=True)
    show = actions.add_parser('show', help='effective policy, origin and internal translation')
    show.add_argument('--json', action='store_true')
    show.set_defaults(func=run)
    for name in ('check', 'set'):
        child = actions.add_parser(name, help='validate without writing' if name == 'check' else 'write one line')
        child.add_argument('spec')
        child.add_argument('--json', action='store_true')
        if name == 'set':
            child.add_argument('--harness', choices=('claude', 'codex'))
        child.set_defaults(func=run)
    reset = actions.add_parser('reset', help='restore Claude 2 + 80%% and Codex 4')
    reset.add_argument('--json', action='store_true')
    reset.set_defaults(func=run)
