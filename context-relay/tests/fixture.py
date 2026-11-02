"""Append synthetic transcript rows and print hook payloads for the tests.

Rows carry only the fields the readers use plus realistic neighbours; no
real transcript content is ever copied into a fixture.

  fixture.py claude-start FILE [--ts ISO]
  fixture.py claude-usage FILE MSG_ID TOKENS [--model M] [--sidechain] [--ts ISO]
  fixture.py claude-boundary FILE UUID [--trigger auto|manual]
  fixture.py claude-text FILE TEXT
  fixture.py codex-meta FILE [--originator O] [--ts ISO]
  fixture.py codex-turn FILE MODEL
  fixture.py codex-tokens FILE INPUT [--window W] [--total T]
  fixture.py codex-compacted FILE WINDOW_ID NUMBER
  fixture.py payload EVENT SESSION TRANSCRIPT [key=json ...]
"""

import datetime
import json
import sys


def now():
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")


def opt(args, name, default=None):
    if name in args:
        i = args.index(name)
        value = args[i + 1] if i + 1 < len(args) and not args[i + 1].startswith("--") else True
        del args[i:i + (2 if value is not True else 1)]
        return value
    return default


def append(path, row):
    with open(path, "a") as handle:
        handle.write(json.dumps(row, separators=(",", ":")) + "\n")


def main(argv):
    cmd, args = argv[0], argv[1:]
    if cmd == "claude-start":
        ts = opt(args, "--ts", now())
        append(args[0], {"type": "queue-operation", "operation": "enqueue"})
        append(args[0], {"type": "user", "timestamp": ts, "message": {"role": "user", "content": "x"}, "isSidechain": False})
    elif cmd == "claude-usage":
        model = opt(args, "--model", "claude-opus-5-5")
        side = opt(args, "--sidechain", False)
        ts = opt(args, "--ts", now())
        path, msg_id, tokens = args[0], args[1], int(args[2])
        cache_read = tokens // 2
        cache_write = tokens - cache_read - 2 if tokens > 2 else 0
        fresh = tokens - cache_read - cache_write
        append(path, {
            "type": "assistant", "isSidechain": bool(side), "timestamp": ts,
            "message": {"id": msg_id, "model": model, "type": "message", "role": "assistant",
                        "usage": {"input_tokens": fresh, "cache_creation_input_tokens": cache_write,
                                  "cache_read_input_tokens": cache_read, "output_tokens": 77}},
        })
    elif cmd == "claude-boundary":
        trigger = opt(args, "--trigger", "auto")
        append(args[0], {"type": "system", "subtype": "compact_boundary", "content": "Conversation compacted",
                         "uuid": args[1], "timestamp": now(),
                         "compactMetadata": {"trigger": trigger, "preTokens": 900000, "postTokens": 9000}})
        append(args[0], {"type": "user", "isCompactSummary": True, "timestamp": now(), "message": {"role": "user", "content": "summary"}})
    elif cmd == "claude-text":
        append(args[0], {"type": "user", "timestamp": now(), "message": {"role": "user", "content": args[1]}})
    elif cmd == "codex-meta":
        originator = opt(args, "--originator", "codex_exec")
        ts = opt(args, "--ts", now())
        append(args[0], {"timestamp": ts, "type": "session_meta",
                         "payload": {"id": "s", "timestamp": ts, "originator": originator,
                                     "source": "exec" if originator == "codex_exec" else "cli", "cli_version": "0.164.0"}})
    elif cmd == "codex-turn":
        append(args[0], {"timestamp": now(), "type": "turn_context", "payload": {"turn_id": "t1", "model": args[1]}})
    elif cmd == "codex-tokens":
        window = int(opt(args, "--window", "258400"))
        total = int(opt(args, "--total", "0"))
        tokens = int(args[1])
        append(args[0], {"timestamp": now(), "type": "event_msg", "payload": {"type": "token_count", "info": {
            "total_token_usage": {"input_tokens": total or tokens * 7, "cached_input_tokens": 0, "output_tokens": 5, "total_tokens": total or tokens * 7},
            "last_token_usage": {"input_tokens": tokens, "cached_input_tokens": tokens // 2, "output_tokens": 5, "total_tokens": tokens + 5},
            "model_context_window": window}}})
    elif cmd == "codex-compacted":
        append(args[0], {"timestamp": now(), "type": "compacted", "payload": {
            "message": "", "window_number": int(args[2]), "window_id": args[1], "previous_window_id": "w0"}})
    elif cmd == "payload":
        event, session, transcript = args[0], args[1], args[2]
        body = {"session_id": session, "transcript_path": transcript, "cwd": "/", "hook_event_name": event}
        for item in args[3:]:
            key, _, value = item.partition("=")
            body[key] = json.loads(value)
        sys.stdout.write(json.dumps(body))
    else:
        raise SystemExit("unknown fixture command: " + cmd)


if __name__ == "__main__":
    main(sys.argv[1:])
