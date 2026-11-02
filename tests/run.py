#!/usr/bin/env python3
"""Run local suites serially. Requires FIRSTMATE_ROOT; starts no live runtime."""
from pathlib import Path
import os
import subprocess
import sys

root = Path(__file__).resolve().parents[1]
if not os.environ.get('FIRSTMATE_ROOT'):
    sys.exit('Set FIRSTMATE_ROOT to a compatible Firstmate checkout for relay library tests')
suites = [root / 'tests/distribution.test.py']
suites += sorted((root / 'context-relay/tests').glob('*.test.py'))
suites += sorted((root / 'context-relay/tests').glob('*.test.sh'))
suites += sorted((root / 'planning/tests').glob('*.test.py'))
failed = []
for suite in suites:
    print('Running ' + str(suite.relative_to(root)), flush=True)
    command = [sys.executable, '-B'] if suite.suffix == '.py' else ['bash']
    try:
        result = subprocess.run(command + [str(suite)], cwd=root, timeout=600)
        code = result.returncode
    except subprocess.TimeoutExpired:
        code = 124
    if code:
        failed.append((str(suite.relative_to(root)), code))
print('Failed: ' + repr(failed) if failed else f'Passed {len(suites)} serial suites', flush=True)
sys.exit(bool(failed))
