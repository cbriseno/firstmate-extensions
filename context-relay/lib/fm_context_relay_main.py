"""Entry point for bin/fm-context-relay and bin/fm-context-relay-hook.

Run with python3 -I, which keeps the script directory off sys.path, so the
package directory is added explicitly.
"""

import os
import sys
import json

sys.dont_write_bytecode = True

if sys.version_info < (3, 8):
    if sys.argv[1:2] == ["hook"]:
        sys.exit(0)
    sys.stderr.write("fm-context-relay requires Python 3.8 or newer\n")
    sys.exit(2)

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from fm_context_relay import cli, hook  # noqa: E402

if __name__ == "__main__":
    if sys.argv[1:2] == ['cli-start']:
        sys.exit(cli.main(json.loads(os.environ.pop('FM_CONTEXT_RELAY_ARGUMENTS')), sanitized=True))
    if sys.argv[1:2] == ["hook"]:
        sys.exit(hook.main())
    sys.exit(cli.main(sys.argv[2:] if sys.argv[1:2] == ["cli"] else sys.argv[1:]))
