"""Observed measurement eligibility; never grants authority or drives a relay."""

from . import common, config, measure


def evidence(record, policy):
    observation = record.get('window_observation') or {}
    scan = record.get('scan') or {}
    comp = record.get('compactions') or {}
    harness = record.get('harness')
    window = observation.get('window_tokens')
    result = dict(admitted=False, evidence=None, reason_code=None,
                  window_tokens=window, window_source=observation.get('source'),
                  counting=comp.get('counting'), counting_signal=comp.get('signal'))

    def refuse(code):
        result['reason_code'] = code
        return result

    if harness not in ('claude', 'codex'):
        return refuse('unsupported_harness')
    if scan.get('caught_up') is not True or scan.get('missing'):
        return refuse('measurement_incomplete')
    if type(window) is not int or window <= 0:
        return refuse('observed_window_missing')
    source = 'claude-statusline' if harness == 'claude' else 'codex-token-count'
    if (observation.get('source') != source or observation.get('harness') != harness
            or not record.get('session_id') or not record.get('owner_pid')
            or observation.get('session_id') != record['session_id']
            or str(observation.get('owner_pid')) != str(record['owner_pid'])
            or not config.normalize_model(record.get('model'))
            or config.normalize_model(observation.get('model')) != config.normalize_model(record['model'])
            or window != record.get('window_tokens_observed')):
        return refuse('observed_window_stale')
    stamp = common.parse_iso(observation.get('at'))
    changed = common.parse_iso(record.get('model_changed_at'))
    if stamp is None or changed is not None and stamp < changed:
        return refuse('observed_window_stale')
    if harness == 'codex' and (
            scan.get('session_id') != record['session_id']
            or scan.get('path') != record.get('transcript_path')
            or any(observation.get(k) != scan.get(k) for k in ('dev', 'ino'))
            or observation.get('transcript_path') != scan.get('path')
            or observation.get('line') != scan.get('window_line')
            or window != scan.get('window_tokens')):
        return refuse('observed_window_stale')
    if not policy or policy.get('harness') != harness:
        return refuse('policy_unresolved')
    # Even OR policies containing a counting branch require a verified count
    # for automatic transfer. Pressure evaluation remains independently useful.
    if policy.get('compactions') is not None:
        counting, signal = measure.compaction_counting(harness, scan)
        if (counting != 'verified' or comp.get('counting') != counting
                or comp.get('signal') != signal
                or type(comp.get('count')) is not int
                or comp['count'] != len(scan.get('compaction_ids') or [])):
            return refuse('compaction_counting_unverified')
    result.update(admitted=True, evidence='observed', reason_code='observed')
    return result


def describe(proof):
    return ('admission %s: window=%s, source=%s, counting signal=%s'
            % (proof['reason_code'], proof['window_tokens'], proof['window_source'], proof['counting_signal']))
