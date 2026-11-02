"""Guided local installation and diagnostics. Never starts a provider or terminal."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'planning/lib'))
from common import PlanningError
VERSION = '0.1.0'
PARTS = ('bin', 'clean-room', 'planning', 'context-relay', 'vendor', 'compatibility.json', 'LICENSE', 'NOTICE')


def runtime():
    base = Path(os.environ.get('XDG_DATA_HOME', str(Path.home() / '.local/share')))
    return base / 'firstmate-extensions/runtime'


def digest(root):
    if any(p.is_symlink() for p in root.rglob('*')):
        raise RuntimeError('Runtime contains a symlink; refusing redirected files')
    return {str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in root.rglob('*') if p.is_file() and p.name != 'installation.json'}


def run(*args, accepted=(0,)):
    result = subprocess.run([str(a) for a in args], timeout=60)
    if result.returncode not in accepted:
        raise RuntimeError(f'Command failed with exit {result.returncode}: {args[0]}')
    return result.returncode


def home_problems(home):
    if home is None:
        return ['Firstmate unavailable: pass --home to a compatible Firstmate checkout']
    sys.path.insert(0, str(ROOT / 'context-relay/lib'))
    from fm_context_relay.install import check_home
    problems = check_home(str(home))
    for name in ('fm-lock-release.sh', 'fm-lock-release.py'):
        if not (home / 'bin' / name).is_file():
            problems.append(f'Firstmate incompatible: missing bin/{name}; lock-release primitive required')
    return problems


def tool_version(name):
    executable = shutil.which(name)
    if not executable:
        return 'missing'
    if name == 'ps':
        return 'unknown (no portable version flag)'
    try:
        result = subprocess.run([executable, '--version'], capture_output=True, text=True, timeout=3)
        lines = (result.stdout or result.stderr).splitlines()
        return lines[0][:160] if result.returncode == 0 and lines else 'unknown'
    except (OSError, subprocess.SubprocessError):
        return 'unknown'


def doctor(args):
    sys.path.insert(0, str(ROOT / 'planning/lib'))
    from core import bundle
    bundle()
    sys.path.insert(0, str(ROOT / 'context-relay/lib'))
    from fm_context_relay import process_inspection as processes
    problems = home_problems(args.home)
    if shutil.which('herdr') is None:
        problems.append('Herdr unavailable: relay requires Herdr 0.9.3/protocol 22; no relay can start')
    if sys.platform == 'linux':
        platform = 'accepted baseline'
        warnings = []
    elif sys.platform == 'darwin':
        platform = 'supported by design; macOS acceptance unverified'
        warnings = ['macOS acceptance has not been recorded', 'Process and terminal safety evidence is unavailable; affected relay operations refuse']
    else:
        platform = 'unverified'
        warnings = ['Platform acceptance has not been recorded', 'Process and terminal safety evidence is unavailable; affected relay operations refuse']
    if shutil.which('git') is None:
        problems.append('Git unavailable: project identity and relay require an approved Git')
    if sys.platform != 'linux' and shutil.which('ps') is None:
        problems.append('ps unavailable: portable process inspection cannot run')
    tools = {
        'python': {'status': 'available', 'required_for': 'all commands', 'minimum': '3.11'},
        'bash': {'status': 'available' if shutil.which('bash') else 'missing', 'required_for': 'relay and planning project verification'},
        'git': {'status': 'available' if shutil.which('git') else 'missing', 'required_for': 'project identity and relay; installation-wide planning remains available'},
        'ps': {'status': 'available' if shutil.which('ps') else 'missing', 'required_for': 'portable process inspection outside Linux'},
        'firstmate': {'status': 'available' if args.home and not home_problems(args.home) else 'missing or incompatible', 'required_for': 'home/project operations and relay'},
        'herdr': {'status': 'available' if shutil.which('herdr') else 'missing', 'required_for': 'relay launch; planning and offline measurement remain available'},
        'provider': {'status': 'not checked', 'required_for': 'native provider hooks and live relay; local planning remains available'},
    }
    if not shutil.which('bash'):
        problems.append('Bash unavailable: relay and planning project verification require Bash')
    state = runtime() / 'installation.json'
    installed = state.is_file()
    if installed:
        record = json.loads(state.read_text())
        if record['files'] != digest(runtime()):
            raise RuntimeError('Installed runtime changed; inspect the installation before reinstalling or removing it')
    for name in ('bash', 'git', 'ps', 'herdr'):
        tools[name]['version'] = tool_version(name)
        tools[name]['category'] = 'required' if name == 'bash' else 'optional by feature'
    tools['python'].update(version=sys.version.split()[0], category='required')
    tools['firstmate'].update(version='unknown (not inspected)', category='optional by feature')
    tools.pop('provider')
    for name in ('codex', 'claude'):
        tools[name] = {'status': 'available' if shutil.which(name) else 'missing',
                       'version': tool_version(name), 'category': 'optional by feature',
                       'required_for': 'native provider hooks and live relay'}
    relay_available = not problems and processes.safety_evidence_available()
    relay_diagnostic = None if processes.safety_evidence_available() else dict(
        code=processes.RELAY_UNAVAILABLE_CODE, message=processes.RELAY_UNAVAILABLE_MESSAGE)
    print(json.dumps({'ok': not args.require_relay or relay_available, 'version': VERSION,
                      'vendor_verified': True, 'installed': installed,
                      'planning': 'available', 'relay': 'prerequisites present' if relay_available else 'unavailable',
                      'relay_diagnostic': relay_diagnostic,
                      'diagnostics': problems, 'platform': platform, 'warnings': warnings, 'tools': tools, 'automatic_default': 'off (warn)',
                      'activation': 'unverified; native hook trust and skill discovery require separate checks',
                      'language': args.language}, indent=2))
    return 2 if args.require_relay and not relay_available else 0


def installation(args):
    dest = runtime()
    if args.home:
        problems = home_problems(args.home)
        if problems:
            raise RuntimeError('; '.join(problems))
    sys.path.insert(0, str(ROOT / 'planning/lib'))
    from core import bundle
    bundle()
    if dest.is_symlink() or dest.resolve() != dest:
        raise RuntimeError('Runtime destination contains a symlink; choose a plain XDG_DATA_HOME')
    if dest.exists():
        record = dest / 'installation.json'
        if not record.is_file() or json.loads(record.read_text())['files'] != digest(dest):
            raise RuntimeError('Existing runtime is unmanaged or changed; inspect it before installing')
        if json.loads(record.read_text()).get('harness') != args.harness:
            raise RuntimeError('A different harness is installed; uninstall before switching')
        if json.loads(record.read_text()).get('home') != (str(args.home) if args.home else None):
            raise RuntimeError('Existing installation belongs to a different home; uninstall it before switching')
        if digest(dest) != {k: v for k, v in digest(ROOT).items() if k.split('/')[0] in PARTS and 'tests' not in Path(k).parts and '__pycache__' not in Path(k).parts}:
            raise RuntimeError('A different release is installed; uninstall it before upgrading')
    else:
        dest.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix='.install-', dir=dest.parent) as temporary:
            staged = Path(temporary) / 'runtime'
            staged.mkdir()
            for part in PARTS:
                source = ROOT / part
                if source.is_dir():
                    shutil.copytree(source, staged / part, ignore=shutil.ignore_patterns('__pycache__', 'tests'))
                else:
                    shutil.copyfile(source, staged / part)
            (staged / 'installation.json').write_text(json.dumps({'version': VERSION, 'home': str(args.home) if args.home else None,
                                                                 'harness': args.harness, 'files': digest(staged)}, indent=2)+'\n')
            staged.rename(dest)
    # Always invoke the installed copy; hooks and skills survive a renamed or removed source clone.
    run(sys.executable, dest / 'planning/bin/install-skills', 'install')
    from common import installation as planning_state
    from config import load, save
    preferences = load(planning_state())
    preferences['language'] = args.language
    save(planning_state(), preferences)
    if args.home:
        run(dest / 'context-relay/bin/fm-context-relay', '--home', args.home, 'install', '--harness', args.harness,
            accepted=(0, 3))
    print('Installed. Installation never enables automatic relay; existing settings are retained. Review native hook trust before activation.')
    return doctor(args)


def uninstallation(args):
    dest = runtime()
    state = dest / 'installation.json'
    if not state.is_file():
        raise RuntimeError('No managed installation found in XDG_DATA_HOME')
    record = json.loads(state.read_text())
    if record['files'] != digest(dest):
        raise RuntimeError('Installed runtime changed; refusing to remove modified files')
    if args.home and str(args.home) != record.get('home'):
        raise RuntimeError('--home does not match the installation record')
    if record.get('home') and (Path(record['home']) / 'state/context-relay/active.json').exists():
        raise RuntimeError('Relay attempt remains active; reach a verified safe terminal state before uninstalling')
    # Preflight skills before mutating either hooks or skills.
    run(sys.executable, dest / 'planning/bin/install-skills', 'uninstall', '--dry-run')
    if record.get('home'):
        run(dest / 'context-relay/bin/fm-context-relay', '--home', record['home'], 'uninstall', '--harness', record['harness'])
    run(sys.executable, dest / 'planning/bin/install-skills', 'uninstall')
    shutil.rmtree(dest)
    print('Uninstalled managed hooks, skills and runtime. Private configuration, history and project artifacts are retained.')
    return 0


def entry():
    if sys.argv[1:] == ['--version']:
        print('Firstmate Extensions ' + VERSION)
        return 0
    if sys.argv[1:2] == ['relay']:
        rest = sys.argv[2:]
        options = argparse.ArgumentParser(add_help=False)
        options.add_argument('--home', default=os.environ.get('FM_HOME'))
        relay_args, _ = options.parse_known_args(rest)
        selected_home = Path(relay_args.home).expanduser().resolve() if relay_args.home else None
        problems = home_problems(selected_home)
        if problems:
            print('Firstmate home unavailable or incompatible: ' + '; '.join(problems), file=sys.stderr)
            return 2
        if rest[:1] == ['status']:
            rest = rest[1:] + ['auto', 'status']
        os.execv(str(ROOT / 'context-relay/bin/fm-context-relay'), [str(ROOT / 'context-relay/bin/fm-context-relay'), *rest])
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=('install', 'uninstall', 'doctor'))
    parser.add_argument('--home', type=lambda p: Path(p).expanduser().resolve(), default=os.environ.get('FM_HOME'))
    parser.add_argument('--harness', choices=('codex', 'claude'), default='codex')
    parser.add_argument('--require-relay', action='store_true')
    parser.add_argument('--language', choices=('en', 'es'), default=os.environ.get('FM_EXTENSION_LANGUAGE', 'en'))
    args = parser.parse_args()
    try:
        return {'install': installation, 'uninstall': uninstallation, 'doctor': doctor}[args.action](args)
    except (OSError, ValueError, RuntimeError, PlanningError, subprocess.TimeoutExpired) as exc:
        print(f'Firstmate Extensions: {exc}', file=sys.stderr)
        return 2
