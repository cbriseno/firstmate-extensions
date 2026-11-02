# Clean-room installation checks

`bin/clean-room` runs one documented scenario in a disposable bubblewrap Linux
namespace. Python 3.11+, bubblewrap, Git, flock and declared system tools are required.
The host Linux kernel and Debian userland are reused; this is not cross-platform
certification or a replacement for publication review.

Use one private task scratch directory for all runs. Before each run check `df`;
the runner also refuses less than 3 GiB free. It serializes runs sharing that task
directory. Concurrent exports on one host must also hold the global
`/tmp/fm-clean-room.lock` with `flock -x`; leave the lock file in place. Local sources must be independent bare Git repositories, without object
alternates. The runner clones them with `--depth 1 --no-local` inside the sandbox.
It never publishes or changes its sources.

```sh
flock -x /tmp/fm-clean-room.lock "$DIST/bin/clean-room" run "$DIST/clean-room/scenarios/extension.json" \
  --task-tmp "$TASK_TMP" --source "extension=$BARE_EXTENSION" --timeout 300
flock -x /tmp/fm-clean-room.lock "$DIST/bin/clean-room" run "$DIST/clean-room/scenarios/extension-with-firstmate.json" \
  --task-tmp "$TASK_TMP" --source "extension=$BARE_EXTENSION" \
  --source "firstmate=$BARE_FIRSTMATE" --timeout 300
flock -x /tmp/fm-clean-room.lock python3 -B "$DIST/clean-room/tests/runner.test.py" --task-tmp "$TASK_TMP"
```

The extension scenario installs from a fresh clone, verifies copied skills and
installed runtime, exercises planning status and installation scope changes,
checks the clear relay refusal without Firstmate, runs doctor, uninstalls, then
clones to a different directory name containing spaces and repeats installation
and removal. The pair scenario installs next to a separately supplied clean
Firstmate clone, prepares Codex hooks, checks hook execution, home/project planning
scopes and native resolution, relay status, missing-Herdr diagnostics and removal.
No model or terminal lifecycle runs. Until the Firstmate source exists, the pair
scenario remains pending; a standalone pass is not a pair pass.

Day Attendant has a separate scenario for its documented local initialization and
core tests; it is not a dependency of the extension.

## Isolation and evidence

Each run has private PID, mount, user, IPC, UTS and network namespaces. HOME and
system temporary directories use tmpfs, with a synthetic `guest` user. No personal
HOME, hooks, skills, cache, provider credentials or terminal sockets are mounted.
Only declared executables and read-only system libraries/resources are exposed.
The runner never downloads dependencies. Network is closed unless the scenario
explicitly enables it; network-enabled scenarios share the host network and need
separate review.

Level 1 runs local installation and fixture checks. Level 2 allows read-only
presence of Codex, Claude Code and Herdr with an expected version. PATH guards
return 126 if they are invoked; even version probes are not run. Such versions
are marked unverified. An installation tree can be mounted under `/opt/runtime`,
never a personal config or credential directory. Scenarios are trusted code, not
a hostile-code boundary against direct execution of mounted implementation files.
Live models and terminal lifecycle require a separate authorized isolated lab;
this runner provides no lifecycle launcher.

The scanner checks file names, content, symlink targets and full command output
after each step, including failures. It detects the real operator user and HOME;
`--patterns FILE` adds private literal strings from a file outside supplied repos.
Reports redact those values. It does not certify unknown secrets, compressed Git
history, images or publication safety. No images are shipped in this distribution.

Only reports and the empty serialization lock persist. The entire sandbox tree is
removed on exit and absence is checked; tmpfs HOME disappears with the namespace.
Timeout and ordinary interruption kill the subprocess group before cleanup.
SIGKILL or host shutdown can prevent any tool from completing its cleanup.
Exit 0 requires both successful scenario and verified cleanup.

Reports contain documentary source, command, expected/observed exit, output tail,
cloned revision, probable failure category and allocated/logical bytes. Output is
scanned in full even though only the last 8192 bytes are retained. Size samples are
per step, not continuous peaks. Tmpfs consumes RAM and possibly swap. Plan roughly
10–30 MB for the extension and 100–200 MB for a Firstmate pair, plus bare sources;
these are estimates, not quotas. Reports provide actual sampled sizes.

## Scenario format

JSON declares `name`, `level`, `network`, `tools`, `repos` and ordered `steps`.
Each tool has `name` and optional `version_args`/`expect_version`. Declare Bash,
sh and Git exactly once, plus env for entrypoint shebangs and all application tools.
Each repo has a stable `id`, cloned under `/work/repos/ID`; pass local sources as
`--source ID=BARE`. Optional public URLs require credential-free HTTPS and explicit
`network: true`. Runtime paths are operator parameters, never stored personal paths.

Each step has `source`, `command`, optional absolute `cwd`, `expect_exit`, `files`,
`check` and `timeout` (1–900 seconds). Steps open fresh Bash shells without rc files;
use explicit cwd and do not assume variables survive between steps. `files` can
use clean environment variables. Checks must return 0. The runner stops on first
failure or private-reference finding. `synthetic: true` labels test substitutes;
their results never establish acceptance of real products. Regression fixtures
exercise isolation, privacy rejection, dependency failure, timeouts and cleanup.
