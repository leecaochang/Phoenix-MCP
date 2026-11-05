"""The per-module coverage floor check reads real configuration and refuses a shortfall."""

import importlib.util
import json
import pathlib

REPO = pathlib.Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location("check_coverage_floors", REPO / "scripts" / "check_coverage_floors.py")
checker = importlib.util.module_from_spec(spec)
spec.loader.exec_module(checker)


def _report(**percent: float) -> dict:
    return {"files": {checker.PACKAGE + module.replace("__", "/") + ".py": {"summary": {"percent_covered": value}}
                      for module, value in percent.items()}}


def test_the_configured_floors_name_real_modules():
    configured = checker.floors(REPO / "pyproject.toml")
    assert {"policy_engine.py", "approvals.py", "access_key_store.py", "request_authority.py"} <= set(configured)
    assert all((REPO / checker.PACKAGE / module).is_file() for module in configured)


def test_a_module_at_its_floor_passes():
    assert checker.failures(_report(approvals=96.0), {"approvals.py": 96}) == []


def test_a_module_under_its_floor_is_named():
    assert checker.failures(_report(tools__recovery=69.99), {"tools/recovery.py": 70}) == [
        "tools/recovery.py: 69.99 is below its floor of 70"]


def test_a_floor_for_an_unmeasured_module_fails():
    """A renamed module must not silently leave the gate."""
    assert checker.failures(_report(), {"approvals.py": 96}) == ["approvals.py: not in the coverage report"]


def test_the_command_exits_nonzero_on_a_shortfall(tmp_path):
    report = tmp_path / "coverage.json"
    report.write_text(json.dumps(_report(approvals=1.0)), encoding="utf-8")
    assert checker.main(["check", str(report)]) == 1
