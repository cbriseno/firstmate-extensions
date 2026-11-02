#!/usr/bin/env python3
"""Restricted static-core helper. No CLI, Git, hooks, network or feature persistence."""
import argparse
import json
import os
from pathlib import Path
import sys


def run():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('stage', choices=('specify', 'clarify', 'plan', 'tasks', 'analyze', 'prerequisites'))
    parser.add_argument('--json', action='store_true')
    parser.add_argument('--paths-only', action='store_true')
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[3]
    value = os.environ.get('SPECIFY_FEATURE_DIRECTORY')
    if not value or os.environ.get('SPECIFY_FEATURE_NO_PERSIST') != '1':
        raise ValueError('Set explicit SPECIFY_FEATURE_DIRECTORY and SPECIFY_FEATURE_NO_PERSIST=1')
    candidate = Path(value)
    if not candidate.is_absolute():
        candidate = root / candidate
    # Check original ancestors before resolving to prevent symlink escapes.
    if any(p.is_symlink() for p in (candidate, *candidate.parents)):
        raise ValueError('Feature path contains a symlink; use a private in-project directory')
    feature = candidate.resolve()
    if root not in feature.parents or feature == root or feature.relative_to(root).parts[0].startswith('.'):
        raise ValueError('Feature must be within a visible directory of this project')
    required = {'specify': (), 'clarify': ('spec',), 'plan': ('spec',),
                'tasks': ('spec', 'plan'), 'analyze': ('spec', 'plan', 'tasks'),
                'prerequisites': ('spec', 'plan', 'tasks')}[args.stage]
    if not args.paths_only:
        for name in required:
            path = feature / f'{name}.md'
            if path.is_symlink() or not path.is_file() or not path.read_text().strip():
                raise ValueError(f'Missing or unsafe {name}.md; complete the preceding stage')
        if args.stage in ('specify', 'plan', 'tasks'):
            dest = feature / f'{args.stage if args.stage != "specify" else "spec"}.md'
            if dest.is_symlink():
                raise ValueError('Authored artifact is a symlink; repair it before continuing')
            feature.mkdir(parents=True, exist_ok=True)
            if not dest.exists():
                template = root / '.specify/templates' / f'{dest.stem}-template.md'
                with dest.open('x') as stream:
                    stream.write(template.read_text())
    result = {'REPO_ROOT': str(root), 'BRANCH': '', 'FEATURE_DIR': str(feature),
              'FEATURE_SPEC': str(feature / 'spec.md'), 'SPEC_FILE': str(feature / 'spec.md'),
              'IMPL_PLAN': str(feature / 'plan.md'), 'TASKS': str(feature / 'tasks.md'),
              'AVAILABLE_DOCS': sorted(p.name for p in feature.iterdir()) if feature.is_dir() else []}
    for kind in ('plan', 'tasks'):
        template = root / '.specify/templates' / f'{kind}-template.md'
        result[f'{kind.upper()}_TEMPLATE'] = str(template)
        result[f'{kind.upper()}_TEMPLATE_CONTENT'] = template.read_text()
    print(json.dumps(result))


if __name__ == '__main__':
    try:
        run()
    except (OSError, ValueError) as exc:
        print(f'speckit-core: {exc}', file=sys.stderr)
        sys.exit(1)
