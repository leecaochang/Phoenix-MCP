"""The pre-push checker reads the test workflow the way GitHub does.

The checker parses the workflow with a small stdlib reader, so it can run before
any environment exists. If that reader drifts from real YAML, the local run
silently checks something other than what GitHub runs.
"""

from __future__ import annotations

import importlib.util
import re
from pathlib import Path
import sys

import yaml

ROOT = Path(__file__).resolve().parent.parent
WORKFLOW = ROOT / ".github" / "workflows" / "tests.yml"


def _checker():
    """The script by path: scripts/ is deliberately not a package."""
    spec = importlib.util.spec_from_file_location("check_before_push", ROOT / "scripts" / "check_before_push.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    # Dataclasses look their module up by name while the class body runs.
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_reader_matches_a_full_yaml_parse_of_the_test_workflow():
    text = WORKFLOW.read_text()
    expected = yaml.safe_load(text)["jobs"]
    parsed = _checker().parse_workflow(text)
    assert [job.id for job in parsed] == list(expected)
    for job in parsed:
        want = expected[job.id]
        assert job.name == want.get("name", job.id)
        assert len(job.steps) == len(want["steps"])
        for step, raw in zip(job.steps, want["steps"]):
            assert step.name == raw.get("name", "")
            assert step.uses == raw.get("uses")
            assert step.run == raw.get("run")
            assert step.with_ == {k: str(v) for k, v in (raw.get("with") or {}).items()}


def test_every_workflow_step_is_run_built_into_an_environment_or_needs_root():
    # A step the checker neither runs nor installs from would pass locally and
    # fail on GitHub, which is the surprise the checker exists to prevent.
    checker = _checker()
    for job in checker.parse_workflow(WORKFLOW.read_text()):
        env_steps, checks = checker.plan(job, full=True)
        covered = {id(s) for s in env_steps + checks}
        for step in job.steps:
            if step.run:
                assert id(step) in covered or checker.needs_root(step), (job.id, step.name)
        # Environment steps run only when the cache is rebuilt, so a check
        # among them would stop running after the first time.
        for step in env_steps:
            assert not re.search(r"-m (pyflakes|mypy|pytest)\b|\bnpm run\b|\bnpx\b", step.run or ""), step.name


def test_quick_mode_drops_only_pytest_steps():
    checker = _checker()
    for job in checker.parse_workflow(WORKFLOW.read_text()):
        _, quick = checker.plan(job, full=False)
        _, full = checker.plan(job, full=True)
        assert [s for s in full if s not in quick] == [s for s in full if checker.runs_pytest(s)]
