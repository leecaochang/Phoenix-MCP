"""Run the versioned offline routing baseline and save reproducible measurements."""
from __future__ import annotations

import argparse
import hashlib
from importlib.metadata import version
import json
from pathlib import Path
import platform
import subprocess
import sys
from time import monotonic
from xml.etree import ElementTree


def main() -> int:
    repo = Path(__file__).resolve().parent.parent
    manifest_path = repo / 'tests/fixtures/routing_acceptance/v1.json'
    content = manifest_path.read_bytes()
    manifest = json.loads(content)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--profile', choices=['all', *manifest['profiles']], default='all')
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    if any((output / name).exists() for name in ('pytest.log', 'junit.xml', 'result.json')):
        parser.error('Use a fresh output directory to preserve earlier evidence')
    groups = manifest['profiles'].values() if args.profile == 'all' else [manifest['profiles'][args.profile]]
    paths = list(dict.fromkeys(path for group in groups for path in group))
    for path in paths:
        if not path.startswith('tests/test_') or not path.endswith('.py') or not (repo / path).is_file():
            parser.error('Invalid acceptance manifest test path')
    def git(*arguments: str) -> str:
        return subprocess.run(['git', *arguments], cwd=repo, check=True, capture_output=True, text=True).stdout.strip()
    metadata = {'baseline_version': manifest['version'], 'manifest_sha256': hashlib.sha256(content).hexdigest(),
                'profile': args.profile, 'git_revision': git('rev-parse', 'HEAD'),
                'dirty': bool(git('status', '--porcelain')), 'python': platform.python_version(),
                'homeassistant': version('homeassistant'), 'test_files': paths}
    start = monotonic()
    with (output / 'pytest.log').open('w') as log:
        result = subprocess.run([sys.executable, '-m', 'pytest', *paths, '-q',
            '--junitxml=' + str(output / 'junit.xml')], cwd=repo, stdout=log, stderr=subprocess.STDOUT)
    metadata.update(exit_code=result.returncode, elapsed_seconds=round(monotonic()-start, 3))
    if (output / 'junit.xml').exists():
        root = ElementTree.parse(output / 'junit.xml').getroot()
        suites = [root] if root.tag == 'testsuite' else list(root.findall('testsuite'))
        totals = {key: sum(int(suite.get(key, '0')) for suite in suites)
                  for key in ('tests', 'failures', 'errors', 'skipped')}
        totals['passed'] = totals['tests']-totals['failures']-totals['errors']-totals['skipped']
        metadata['totals'] = totals
    (output / 'result.json').write_text(json.dumps(metadata, indent=2)+'\n')
    print(json.dumps(metadata, indent=2))
    return result.returncode


if __name__ == '__main__':
    raise SystemExit(main())
