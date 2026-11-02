"""Optional user event consumer. Empty argv means no external delivery.

No shell, service integration, retries or credentials. Consumer failures cannot
change an assisted transition, output to the model, or start another attempt.
"""
import json
import subprocess
from pathlib import Path

from . import common, config


def emit(home, kind, attempt):
    paths = common.Paths(str(home))
    try:
        loaded = config.load(paths)
        if loaded.status != 'ok' or loaded.errors:
            return
        argv = loaded.settings.value('relay_event_command')
        if not argv:
            return
        payload = dict(schema='fm-relay-event.v1', event=kind, attempt=attempt, at=common.iso())
        result = subprocess.run(argv, input=json.dumps(payload), text=True, cwd=home,
                                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=1)
        if result.returncode:
            raise RuntimeError('consumidor rc=%d' % result.returncode)
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as exc:
        # Consumer failure is operational evidence only, never a transition.
        try:
            common.append_line(str(Path(paths.data_dir)/'event-consumer-errors.jsonl'),
                               json.dumps(dict(at=common.iso(), event=kind, error=str(exc))))
        except OSError:
            pass
