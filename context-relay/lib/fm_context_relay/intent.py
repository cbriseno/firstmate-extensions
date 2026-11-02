"""Conservative, read-only routing of standalone relay instructions.

This is a phrase aid, never proof of who sent a message or an executor.
The primary must still check current captain intent and runtime support.
"""
import json
import re
import unicodedata

from . import thresholds


def classify(message):
    text = ''.join(c for c in unicodedata.normalize('NFD', message.casefold())
                   if not unicodedata.combining(c))
    text = re.sub(r'\s+', ' ', text).strip()
    # Questions, quotes, explanations and compound instructions never match
    # the complete affirmative grammar below.
    text = text.strip(' .!¡')
    text = re.sub(r'^(?:please |por favor[, ]+)', '', text)
    text = re.sub(r'[, ]+(?:please|por favor)$', '', text)
    result = dict(action='none', captain_words=message, executes=False)
    for action, verbs in (
        ('enable', r'(?:activar|activa|active|habilitar|habilita|enciende)'),
        ('disable', r'(?:desactivar|desactiva|desactive|deshabilitar|deshabilita|apaga)'),
    ):
        spanish = r'(?:quiero (?:que )?)?' + verbs + r' (?:el )?relevo automatico'
        english = r'(?:i want to )?' + (
            r'(?:enable|turn on|switch on) (?:the )?automatic relay' if action == 'enable'
            else r'(?:disable|turn off|switch off) (?:the )?automatic relay')
        if re.fullmatch(spanish + '|' + english, text):
            result['action'] = action
            return result
    if text in ('estado del relevo', 'relay status', 'show relay status', 'muestra el estado del relevo'):
        result['action'] = 'status'
    elif text == 'relevo':
        result['action'] = 'assisted'
    else:
        try:
            # Preserve decimal notation and spelling for the existing parser.
            entries = thresholds.parse_lines(message)
        except ValueError:
            pass
        else:
            if entries:
                result.update(action='thresholds', entries=entries)
    return result


def run(args, paths):
    del paths
    print(json.dumps(classify(args.message), ensure_ascii=False))
    return 0


def add_parser(sub):
    parser = sub.add_parser('relay-intent', help='classify a standalone phrase without changing state or starting relay')
    parser.add_argument('message', help='current message, passed verbatim as one argument')
    parser.set_defaults(func=run)
