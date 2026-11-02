"""Per-session pressure record: thresholds, their history and warnings.

The record lives in $FM_HOME/state/context-relay/sessions/<harness>-<id>.json,
outside the model context, so a compaction cannot erase a request. A request
only ever rises (0 none, 1 early warning, 2 check current margin). This is a
historical high-water mark, not authority to replace a healthy session.
Each warning key is delivered at most once per session.
"""

from . import common
from . import measure

SCHEMA = "fm-context-relay-session.v1"
LEVEL_LABELS = {1: "aviso temprano", 2: "caution: check current headroom"}
MAX_DELIVERIES = 50


def new_record(harness, session_id, now):
    return {
        "schema": SCHEMA,
        "harness": harness,
        "session_id": session_id,
        "first_seen": common.iso(now),
        "updated_at": common.iso(now),
        "last_event": None,
        "owner_pid": None,
        "transcript_path": None,
        "model": None,
        "models_seen": [],
        "resident": {"status": "no_data", "reason": "no_call_yet", "tokens": None},
        "compactions": {"count": 0, "counting": None, "signal": None},
        "compaction_signal": None,
        "scan": measure.new_scan(),
        "effective": None,
        "relay": None,
        "request": {"level": 0, "opened_at": None, "escalated_at": None, "reasons": []},
        "pending": [],
        "delivered": [],
        "deliveries": [],
        "checkpoint": {"last_attempt_at": None, "last_path": None, "last_error": None, "count": 0},
    }


def load_record(paths, key, harness, session_id, now):
    record = common.read_json(paths.session_record(key))
    if not isinstance(record, dict) or record.get("schema") != SCHEMA or record.get("session_id") != session_id:
        return new_record(harness, session_id, now)
    return record


def save_record(paths, key, record):
    common.atomic_write_json(paths.session_record(key), record, 0o600)


def refresh_measurement(record, harness, budget_seconds, compaction_event=None, now=None):
    """Scan new transcript lines and update resident context and compactions.

    compaction_event names a SessionStart(compact) or PostCompact firing:
    proof that a real compaction just happened. Every model call already in
    the transcript then predates it, so the latest figure reads as no data
    until a newer call appears, even if the boundary row is not written yet.
    Returns the number of newly counted compactions.
    """
    path = record.get("transcript_path")
    if not path:
        record["resident"] = {"status": "no_data", "reason": "transcript_missing", "tokens": None}
        return 0
    before = len((record.get("scan") or {}).get("compaction_ids") or [])
    record["scan"] = measure.scan(record.get("scan"), path, harness, budget_seconds)
    scan = record["scan"]
    latest_id = (scan.get("last_usage") or {}).get("id")
    if compaction_event:
        record["compaction_signal"] = {"at": common.iso(now), "event": compaction_event, "usage_id": latest_id}
    elif record.get("compaction_signal") and record["compaction_signal"].get("usage_id") != latest_id:
        record["compaction_signal"] = None
    signal = record.get("compaction_signal")
    summary = measure.summarize(harness, scan, signal.get("usage_id") if signal else None)
    if signal and signal.get("usage_id") is None and summary["resident"]["status"] == "ok":
        summary["resident"] = {"status": "no_data", "reason": "after_compaction", "tokens": None}
    record["resident"] = summary["resident"]
    record["compactions"] = {
        "count": summary["compactions"]["count"],
        "counting": summary["compactions"]["counting"],
        "signal": summary["compactions"]["signal"],
    }
    if summary["window_tokens"]:
        record["window_tokens_observed"] = summary["window_tokens"]
    model = scan.get("model")
    if model and model != record.get("model"):
        if not summary["window_tokens"]:
            record.pop("window_tokens_observed", None)
        record["model"] = model
        if model not in record["models_seen"]:
            record["models_seen"].append(model)
    if record.get("session_started_at") is None and scan.get("started_at"):
        record["session_started_at"] = scan["started_at"]
    return max(0, record["compactions"]["count"] - before)


def _age_hours(record, now):
    started = common.parse_iso(record.get("session_started_at")) or common.parse_iso(record.get("first_seen"))
    if started is None:
        return None
    return (now - started).total_seconds() / 3600.0


def _add_pending(record, key, text):
    if key in record["delivered"] or any(item.get("key") == key for item in record["pending"]):
        return False
    record["pending"].append({"key": key, "text": text})
    return True


def evaluate(record, load_result, harness, now, cli, new_compactions=0):
    """Update the effective trigger, the request level and pending warnings."""
    settings = load_result.settings
    model = record.get("model") or ""
    eff = settings.for_model(model)
    counting = (record.get("compactions") or {}).get("counting")
    trigger = eff["trigger"]
    effective_trigger = trigger
    fallback = None
    if trigger in ("compactions", "first") and counting != "verified":
        effective_trigger = "tokens"
        fallback = (record.get("compactions") or {}).get("signal") or "no verified count"
    caught_up = bool((record.get("scan") or {}).get("caught_up"))
    resident = record.get("resident") or {}
    reasons = []
    if effective_trigger in ("tokens", "first") and resident.get("status") == "ok" and eff["request_tokens"]:
        tokens = resident["tokens"]
        stop = eff["stop_new_work_tokens"]
        if stop and tokens >= stop:
            reasons.append((2, "tokens", tokens, stop))
        elif tokens >= eff["request_tokens"]:
            reasons.append((1, "tokens", tokens, eff["request_tokens"]))
    if effective_trigger in ("compactions", "first") and counting == "verified" and caught_up:
        count = record["compactions"]["count"]
        limit = eff["compactions_before_relay"]
        if count >= limit + 1:
            reasons.append((2, "compactions", count, limit + 1))
        elif count >= limit:
            reasons.append((1, "compactions", count, limit))
    max_hours = settings.value("max_session_hours")
    age = _age_hours(record, now)
    if age is not None and age >= max_hours:
        reasons.append((1, "max_session_hours", round(age, 1), max_hours))
    record["effective"] = {
        "model": eff["model"] or None,
        "known_model": eff["known"],
        "trigger": trigger,
        "effective_trigger": effective_trigger,
        "fallback": fallback,
        "compactions_before_relay": eff["compactions_before_relay"],
        "request_tokens": eff["request_tokens"],
        "stop_new_work_tokens": eff["stop_new_work_tokens"],
        "window_tokens": eff["window_tokens"],
        "relay_mode": settings.value("relay_mode"),
        "settings": load_result.using,
        "age_hours": round(age, 2) if age is not None else None,
    }
    evaluate_relay_policy(record, settings, harness, now)
    if settings.value("relay_mode") == "assisted":
        _add_pending(record, "relay-mode:assisted", _relay_mode_text(record))
    sid = record["session_id"][:8]
    if load_result.status == "invalid" and load_result.sha:
        more = len(load_result.errors) - 1
        _add_pending(
            record,
            "config:%s" % load_result.sha[:12],
            "Relay configuration is invalid (%s%s); continuing with %s. Review with `%s show`; for threshold policy use `umbral check`, `umbral set` or `umbral reset`. Propose corrections to the operator before writing (`%s set ... --reason ... --approved ...` for JSON)."
            % (
                load_result.errors[0],
                " and %d more error(s)" % more if more > 0 else "",
                "last valid values" if load_result.using == "last-valid" else "default values",
                cli,
                cli,
            ),
        )
    if model and not eff["known"]:
        _add_pending(
            record,
            "unknown-model:%s" % eff["model"],
            "Model %s has no settings in config/context-relay.json: warning only (unknown_model=warn_only), trigger %s and no token thresholds. Propose additions to the operator with `%s show` evidence and write only after explicit approval (`%s set models.%s.request_tokens <n> --reason ... --approved ...`)."
            % (eff["model"], trigger, cli, cli, eff["model"]),
        )
    if fallback and eff["request_tokens"]:
        _add_pending(
            record,
            "fallback:%s" % eff["model"],
            "Compaction counting is unverified here (%s); model %s uses the token trigger meanwhile."
            % (fallback, eff["model"] or "actual"),
        )
    observed = record.get("window_tokens_observed")
    if observed and eff["known"] and (
        (eff["window_tokens"] and observed != eff["window_tokens"])
        or any(eff[k] and eff[k] >= observed for k in ("request_tokens", "stop_new_work_tokens"))
    ):
        _add_pending(
            record, "window:%s:%s" % (eff["model"], observed),
            "Observed window for %s is %s; configured window is %s. Validate thresholds with `%s validate` and propose changes to the operator before writing."
            % (eff["model"], common.format_tokens(observed), common.format_tokens(eff["window_tokens"]), cli),
        )
    new_level = max([r[0] for r in reasons] or [0])
    request = record["request"]
    escalated = False
    if new_level > request["level"]:
        stamp = common.iso(now)
        if request["level"] == 0:
            request["opened_at"] = stamp
        request["escalated_at"] = stamp
        request["level"] = new_level
        for level, trig, value, threshold in reasons:
            request["reasons"].append(
                {"level": level, "trigger": trig, "value": value, "threshold": threshold, "model": eff["model"] or None, "at": stamp}
            )
        del request["reasons"][:-20]
        record["pending"] = [p for p in record["pending"] if not p["key"].startswith("level:")]
        top = [r for r in reasons if r[0] == new_level]
        _add_pending(record, "level:%d" % new_level, _level_text(new_level, top, record, sid))
        escalated = True
    if new_compactions and request["level"] >= 1 and not escalated:
        count = record["compactions"]["count"]
        _add_pending(
            record,
            "reminder:compaction:%d" % count,
            _reminder_text(record),
        )
    # Pending text is durable too. Upgrade old, undelivered instructions in
    # place, keeping their keys and delivered history (no repeat injection).
    for warning in record["pending"]:
        if warning["key"] in ("level:1", "level:2"):
            level = int(warning["key"][-1])
            top = [(level, r["trigger"], r["value"], r["threshold"])
                   for r in request["reasons"] if r["level"] == level]
            warning["text"] = _level_text(level, top, record, sid)
        elif warning["key"].startswith("reminder:compaction:"):
            warning["text"] = _reminder_text(record)


def _reminder_text(record):
    request = record["request"]
    return (
        "Post-compaction reminder: historical warning level %d (%s) since %s. Session compactions: %d. "
        "The previous figure does not measure current pressure: reevaluate with the next valid resident measurement. "
        "Preserve session continuity; this history does not require relay."
        % (request["level"], LEVEL_LABELS[request["level"]], request["opened_at"], record["compactions"]["count"])
    )


def relay_policy(settings, harness):
    configured = settings.value("relay_policy").get(harness)
    if configured is None:
        return None
    percent = configured.get("min_context_percent")
    policy = {"harness": harness, "compactions": configured.get("compactions"),
              "min_context_percent": float(percent) if percent is not None else None}
    if "min_context_tokens" in configured:
        policy["min_context_tokens"] = configured["min_context_tokens"]
    if configured.get("operator") == "or":
        policy["operator"] = "or"
    return policy


def evaluate_relay_policy(record, settings, harness, now):
    """Evaluate relay_policy only; hooks never prepare or start a handoff.

    Each branch uses verified, caught-up evidence. Combined context uses the
    reader's post-boundary resident figure. OR can match one verified branch
    even when the other has no evidence. The first match is durable.
    """
    policy = relay_policy(settings, harness)
    previous = record.get("relay") or {}
    record["pending"] = [p for p in record["pending"] if not p["key"].startswith(("relay-policy:", "relay-current:"))]
    if policy is None:
        record["relay"] = {"policy": None, "pending": False, "status": "disabled"}
        return
    percent = policy["min_context_percent"]
    tokens = policy.get("min_context_tokens")
    count = policy["compactions"]
    policy_id = common.sha256_bytes(common.dump_json(policy))[:12]
    comp = record.get("compactions") or {}
    resident = record.get("resident") or {}
    observed = record.get("window_tokens_observed")
    window = observed or (record.get("effective") or {}).get("window_tokens")
    source = "observed" if observed else "configured" if window else None
    comp_status = ("unverified_compactions" if comp.get("counting") != "verified" else
                   "waiting_compactions" if count is not None and comp.get("count", 0) < count else "matched")
    context_status = "matched"
    has_context = percent is not None or tokens is not None
    if has_context:
        if resident.get("status") != "ok":
            context_status = "waiting_resident"
        elif percent is not None and not window:
            context_status = "window_unknown"
        elif (percent is not None and resident["tokens"] * 100 < window * percent
              or tokens is not None and resident["tokens"] < tokens):
            context_status = "waiting_occupancy"
    branches = {}
    if count is not None:
        branches["compactaciones"] = comp_status
    if has_context:
        branches["umbral"] = context_status
    fired = [name for name, value in branches.items() if value == "matched"]
    status = next((v for v in branches.values() if v != "matched"), "matched")
    if policy.get("operator") == "or" and fired:
        status = "matched"
    if not (record.get("scan") or {}).get("caught_up"):
        status, fired = "catching_up", []
    matched = status == "matched"
    same_policy = previous.get("policy") == policy
    pending = matched or (same_policy and bool(previous.get("pending")))
    evidence = previous.get("matched_evidence") if same_policy else None
    if matched and not evidence:
        evidence = {"at": common.iso(now), "compactions": comp.get("count"),
                    "resident_tokens": resident.get("tokens"), "window_tokens": window, "window_source": source,
                    "fired_triggers": fired}
    record["relay"] = {
        "policy": policy, "policy_id": policy_id, "pending": pending, "current_match": matched,
        "status": "pending_safe_point" if pending else status,
        "pending_since": (previous.get("pending_since") if same_policy else None) or (common.iso(now) if pending else None),
        "compactions": comp.get("count", 0), "counting": comp.get("counting"),
        "window_tokens": window, "window_source": source, "branches": branches,
        "fired_triggers": (evidence or {}).get("fired_triggers", []),
        "matched_evidence": evidence,
    }
    _add_pending(record, "relay-current:%s" % policy_id, _policy_current_text(record))
    if pending:
        _add_pending(
            record, "relay-policy:%s" % policy_id,
            "Relay policy reached: %s. Trigger: %s. Relay awaiting safe point since %s. "
            "Finish current tasks safely or save state and pause safely before handover. "
            "Do not start another substantive task before reaching that safe point. %s"
            % (_policy_condition(policy), ", ".join(record["relay"]["fired_triggers"]) or "previous verified policy",
               record["relay"]["pending_since"], _relay_mode_text(record)),
        )


def _policy_condition(policy):
    parts = []
    if policy["compactions"] is not None:
        parts.append(">=%d verified compactions" % policy["compactions"])
    if policy.get("min_context_percent") is not None:
        parts.append("input >= %g%% of model window" % policy["min_context_percent"])
    if policy.get("min_context_tokens") is not None:
        parts.append("resident context >= %d tokens" % policy["min_context_tokens"])
    return (" o " if policy.get("operator") == "or" else " then ").join(parts)


def _relay_mode_text(record):
    if (record.get('effective') or {}).get('relay_mode') == 'auto':
        return ('Auto mode with durable authority: save decisions and reach the safe point. '
                'Automatic entry uses the same preparation and durable confirmation, without another operator confirmation; '
                'respect the automatic notice and immediate kill switch.')
    if (record.get("effective") or {}).get("relay_mode") == "assisted":
        return (
            "Assisted mode: the operator can request relay in chat; the single entrypoint relay start "
            "prepares the safe point and shows the package. Preparation never releases the lock or starts a successor. "
            "After the summary require explicit operator confirmation before retirement and launch. "
            "See relay start --help; do not run automatic relay."
        )
    return "Warn mode: do not run automatic relay or interrupt a task midway; preserve required transfer authorization."


def _policy_current_text(record):
    """One natural, durable announcement per policy, including live upgrades."""
    relay = record["relay"]
    policy = relay["policy"]
    condition = _policy_condition(policy)
    resident = record.get("resident") or {}
    context = common.format_tokens(resident.get("tokens")) if resident.get("status") == "ok" else "no valid data"
    capacity = "window unknown"
    if policy.get('min_context_percent') is not None and not relay['window_tokens']:
        capacity += ': cannot resolve percentage; context_window_size or window_tokens missing'
    if relay["window_tokens"]:
        capacity = "window %s (%s)" % (common.format_tokens(relay["window_tokens"]), relay["window_source"])
        if relay["window_source"] == "observed" and resident.get("status") == "ok":
            capacity += ", %.1f%% of input/window" % (100.0 * resident["tokens"] / relay["window_tokens"])
    action = "Finish or durably pause current tasks to reach the safe point." if relay["pending"] else "Continue useful work until the predicate is met."
    return (
        "Current relay policy for %s: %s. Count %d/%s (%s), resident context %s, %s; state %s; "
        "relay pending=%s. Previous pressure warnings and historical levels do not block admission "
        "or request another transfer before this policy is met. %s Allow native compaction. "
        "Do not interrupt a task midway. %s"
        % (policy["harness"], condition, relay["compactions"], policy["compactions"], relay["counting"],
           context, capacity, relay["status"], relay["pending"], action, _relay_mode_text(record))
    )


def _reason_text(reason):
    _, trigger, value, threshold = reason
    if trigger == "tokens":
        return "resident context %s >= %s" % (common.format_tokens(value), common.format_tokens(threshold))
    if trigger == "compactions":
        return "%d compactions (limit %d)" % (value, threshold)
    return "session age %s h (maximum %s h)" % (value, threshold)


def _level_text(level, reasons, record, sid):
    why = "; ".join(_reason_text(r) for r in reasons)
    resident = record.get("resident") or {}
    context = common.format_tokens(resident.get("tokens")) if resident.get("status") == "ok" else "no data (%s)" % resident.get("reason")
    eff = record.get("effective") or {}
    mode_label = "phase 2, mode assisted" if eff.get("relay_mode") == "assisted" else "phase 1, mode warn"
    observed = record.get("window_tokens_observed")
    window = observed or eff.get("window_tokens")
    capacity = "window unknown"
    if window:
        capacity = "window %s (%s)" % (
            common.format_tokens(window), "observed by harness" if observed else "configured, unverified",
        )
        if observed and resident.get("status") == "ok":
            capacity += ", %.1f%% of input/window" % (100.0 * resident["tokens"] / observed)
    facts = "Session %s, model %s, resident context %s, %s, compactions %d (%s)." % (
        sid,
        record.get("model") or "unknown",
        context,
        capacity,
        (record.get("compactions") or {}).get("count", 0),
        (record.get("compactions") or {}).get("counting") or "unverified",
    )
    relay = record.get("relay") or {}
    if relay.get("policy"):
        facts += " Relay policy: %s; pressure level does not replace its predicate." % relay["status"]
    if level == 1:
        return (
            "Early context warning (%s, level 1). Observed threshold: %s. %s "
            "Continue useful work and allow native compaction. Preserve the session and its continuity. "
            "This notice does not order stow, close or relay; replacement requires evidence of persistent pressure "
            "or continuity failures and the corresponding authorization." % (mode_label, why, facts)
        )
    stop = eff.get("stop_new_work_tokens")
    guard = (
        "Do not admit new substantive work while a valid resident measurement reaches %s tokens; "
        "allow native compaction and reevaluate afterward. " % common.format_tokens(stop)
        if stop else
        "Check resident headroom before expanding work and allow native compaction. "
    )
    if relay.get("policy"):
        guard = (
            "This pressure threshold is informational under explicit relay policy: continue useful work "
            "until its safe point and allow native compaction. Old token thresholds do not block "
            "admission or authorize transfer before policy is met. Observe an actual harness limit "
            "if its native signal indicates one; this notice does not prove that limit. "
        )
    return (
        "Caution threshold recorded (%s, level 2). Reason: %s. %s %s"
        "Historical compaction count does not prove current saturation. "
        "This notice does not authorize stow, close or session replacement." % (mode_label, why, facts, guard)
    )


def take_pending(record, event, now):
    """Mark every pending warning delivered and return the injection text."""
    if not record["pending"]:
        return None
    stamp = common.iso(now)
    lines = []
    for item in record["pending"]:
        record["delivered"].append(item["key"])
        record["deliveries"].append({"key": item["key"], "event": event, "at": stamp})
        lines.append(item["text"])
    del record["deliveries"][:-MAX_DELIVERIES]
    record["pending"] = []
    return "[context-relay] " + "\n[context-relay] ".join(lines)
