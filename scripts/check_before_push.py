"""Run the checks GitHub runs on a push, locally, on a clean copy of one commit.

Jobs and steps are read from that commit's .github/workflows/tests.yml, so the
local run follows the workflow as it changes. Each Python job's environment is
built from its own install steps and cached until those inputs change. Hassfest
runs from the newest published Home Assistant release, the source of the
hassfest image that the workflow and the HACS default repository both use. The
HACS action reads the GitHub repository page itself, so it only runs there.

Quick mode leaves out every step that runs pytest; --full runs all of them.
Jobs run side by side; --serial runs one at a time.

Run: python3 scripts/check_before_push.py [--full] [--serial] [REF]
     python3 scripts/check_before_push.py --install-hook
"""

from __future__ import annotations

import argparse
import concurrent.futures
import dataclasses
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import threading
import time
import urllib.request

WORKFLOW = ".github/workflows/tests.yml"
CHECKER = "scripts/check_before_push.py"
CORE_REPO = "https://github.com/home-assistant/core.git"
RELEASES_API = "https://api.github.com/repos/home-assistant/core/releases?per_page=10"
STAMP = ".phoenix-check-key"
KEEP_LOG_RUNS = 10
FAILURE_TAIL = 40
HOOK_MARKER = "# Installed by scripts/check_before_push.py --install-hook"

HOOK = f"""#!/bin/sh
{HOOK_MARKER}
# Runs the quick checks on each branch commit being pushed, with the checker
# stored in that commit. Skip once with: git push --no-verify
zero=0000000000000000000000000000000000000000
py=$(command -v python3.14 || command -v python3)
seen=""
status=0
while read -r local_ref local_sha remote_ref remote_sha; do
  case "$local_ref" in refs/tags/*) continue ;; esac
  [ "$local_sha" = "$zero" ] && continue
  case " $seen " in *" $local_sha "*) continue ;; esac
  seen="$seen $local_sha"
  if ! git cat-file -e "$local_sha:{CHECKER}" 2>/dev/null; then
    echo "check_before_push: $local_ref has no checker, not checked"
    continue
  fi
  checker=$(mktemp) || exit 1
  git show "$local_sha:{CHECKER}" > "$checker"
  "$py" "$checker" "$local_sha" < /dev/null || status=1
  rm -f "$checker"
done
if [ $status -ne 0 ]; then
  echo "Push stopped by the pre-push checks. To push anyway: git push --no-verify"
fi
exit $status
"""


@dataclasses.dataclass
class Step:
    name: str = ""
    run: str | None = None
    uses: str | None = None
    with_: dict[str, str] = dataclasses.field(default_factory=dict)


@dataclasses.dataclass
class Job:
    id: str
    name: str = ""
    steps: list[Step] = dataclasses.field(default_factory=list)


@dataclasses.dataclass
class Result:
    label: str
    status: str
    seconds: float = 0.0
    log: Path | None = None
    note: str = ""


def _indent(line: str) -> int:
    return len(line) - len(line.lstrip(" "))


def _scalar(text: str) -> str:
    """A plain or quoted YAML scalar, without a trailing comment."""
    text = text.strip()
    if text[:1] in ("'", '"'):
        end = text.find(text[0], 1)
        if end > 0:
            return text[1:end]
    return re.split(r"\s+#", text, maxsplit=1)[0].strip()


def parse_workflow(text: str) -> list[Job]:
    """The jobs of a GitHub workflow, read from the YAML subset workflows here use.

    Covers the job mappings, the step list, `with:` maps and `run: |` literal
    blocks. The standard library has no YAML parser, and the checker must run
    before any environment exists.
    """
    lines = text.splitlines()
    jobs: list[Job] = []
    job: Job | None = None
    step: Step | None = None
    in_jobs = in_with = False
    i = 0
    while i < len(lines):
        line = lines[i]
        i += 1
        stripped = line.strip()
        indent = _indent(line)
        if not stripped or stripped.startswith("#"):
            continue
        if indent == 0:
            in_jobs = stripped == "jobs:"
            continue
        if not in_jobs:
            continue
        if indent == 2:
            job = Job(id=stripped.rstrip(":"))
            jobs.append(job)
            step = None
            continue
        if job is None:
            continue
        if indent == 4:
            key, _, value = stripped.partition(":")
            if key == "name":
                job.name = _scalar(value)
            step = None
            continue
        if indent == 6 and stripped.startswith("- "):
            step = Step()
            job.steps.append(step)
            stripped, indent = stripped[2:].strip(), 8
        if step is None:
            continue
        key, _, value = stripped.partition(":")
        if indent == 8:
            in_with = key == "with"
            value = value.strip()
            if key == "run" and value in ("|", "|-"):
                block: list[str] = []
                while i < len(lines) and (not lines[i].strip() or _indent(lines[i]) > 8):
                    block.append(lines[i])
                    i += 1
                while block and not block[-1].strip():
                    block.pop()
                width = min(_indent(b) for b in block if b.strip())
                step.run = "\n".join(b[width:] for b in block) + ("\n" if value == "|" else "")
            elif key == "name":
                step.name = _scalar(value)
            elif key == "run":
                step.run = _scalar(value)
            elif key == "uses":
                step.uses = _scalar(value)
        elif indent == 10 and in_with:
            step.with_[key] = _scalar(value)
    for parsed in jobs:
        parsed.name = parsed.name or parsed.id
    return jobs


def python_version(job: Job) -> str | None:
    for step in job.steps:
        if step.uses and step.uses.startswith("actions/setup-python"):
            return step.with_.get("python-version")
    return None


def is_install(step: Step) -> bool:
    return step.name.startswith("Install")


def needs_root(step: Step) -> bool:
    return (step.run or "").lstrip().startswith("sudo ")


def runs_pytest(step: Step) -> bool:
    return re.search(r"-m pytest\b", step.run or "") is not None


def plan(job: Job, full: bool) -> tuple[list[Step], list[Step]]:
    """(environment steps, check steps) for one job.

    A Python job's install steps build its cached environment instead of running
    every time. Steps that need root install system packages a developer machine
    already has, so they are left out.
    """
    runnable = [s for s in job.steps if s.run and not needs_root(s)]
    if python_version(job):
        env_steps = [s for s in runnable if is_install(s)]
        checks = [s for s in runnable if not is_install(s)]
    else:
        env_steps, checks = [], runnable
    if not full:
        checks = [s for s in checks if not runs_pytest(s)]
    return env_steps, checks


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True, text=True).stdout.strip()


def _child_env(path_dirs: list[Path]) -> dict[str, str]:
    env = dict(os.environ)
    env.pop("VIRTUAL_ENV", None)
    env["CI"] = "true"
    env["PATH"] = os.pathsep.join([*(str(p) for p in path_dirs), env.get("PATH", "")])
    return env


def _run(command: str | list[str], cwd: Path, log: Path, path_dirs: list[Path] | None = None) -> bool:
    """Run a workflow shell block (the runner's bash flags) or an argv, logging all output."""
    argv = ["bash", "--noprofile", "--norc", "-eo", "pipefail", "-c", command] if isinstance(command, str) else command
    with log.open("a") as fh:
        fh.write(f"$ {command if isinstance(command, str) else ' '.join(command)}\n")
        fh.flush()
        code = subprocess.run(argv, cwd=cwd, env=_child_env(path_dirs or []), stdout=fh,
                              stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL).returncode
        fh.write(f"[exit {code}]\n\n")
    return code == 0


def _interpreter(version: str) -> str:
    """The installed python<version> when there is one, otherwise uv's choice.

    The workflow gets the newest patch release; an older one that uv happens to
    have downloaded can behave differently (early 3.14 releases break mock autospec
    on forward-referenced annotations), so an installed interpreter wins.
    """
    return shutil.which(f"python{version}") or version


def _interpreter_id(version: str) -> str:
    exe = _interpreter(version)
    if exe == version:
        return version
    out = subprocess.run([exe, "-c", "import sys; print(sys.version)"], capture_output=True, text=True)
    return f"{exe} {out.stdout.strip()}"


def _make_venv(path: Path, version: str, log: Path) -> bool:
    exe = _interpreter(version)
    if shutil.which("uv"):
        return _run(["uv", "venv", "--seed", "--quiet", "-p", exe, str(path)], path.parent, log)
    if exe == version:
        with log.open("a") as fh:
            fh.write(f"Python {version} not found; install it or uv\n")
        return False
    return _run([exe, "-m", "venv", str(path)], path.parent, log)


def _env_key(copy: Path, version: str, steps: list[Step]) -> str:
    """Fingerprint of everything an environment is built from."""
    digest = hashlib.sha256(_interpreter_id(version).encode())
    commands = "\n".join(s.run or "" for s in steps)
    digest.update(commands.encode())
    for token in sorted(set(re.findall(r"[\w./-]+\.(?:txt|py|toml)\b", commands))):
        if (copy / token).is_file():
            digest.update(token.encode() + (copy / token).read_bytes())
    for manifest in sorted(copy.glob("custom_components/*/manifest.json")):
        data = json.loads(manifest.read_text())
        fields = {k: data.get(k) for k in ("requirements", "dependencies", "after_dependencies")}
        digest.update(json.dumps(fields, sort_keys=True).encode())
    return digest.hexdigest()


def _node_bin(copy: Path) -> Path | None:
    """A Node binary directory matching .nvmrc's major version."""
    want = (copy / ".nvmrc").read_text().strip().lstrip("v").split(".")[0]

    def major(node: Path) -> str:
        out = subprocess.run([str(node), "--version"], capture_output=True, text=True).stdout
        return out.strip().lstrip("v").split(".")[0]

    found = shutil.which("node")
    candidates = [Path(found)] if found else []
    candidates += sorted((Path.home() / ".nvm/versions/node").glob(f"v{want}.*/bin/node"), reverse=True)
    candidates += [Path(f"/opt/homebrew/opt/node@{want}/bin/node"), Path(f"/usr/local/opt/node@{want}/bin/node")]
    for node in candidates:
        if node.is_file() and major(node) == want:
            return node.parent
    return None


class Runner:
    """Runs the planned jobs and reports each step as it finishes."""

    def __init__(self, cache: Path, logs: Path) -> None:
        self.cache = cache
        self.logs = logs
        self.results: list[Result] = []
        self._lock = threading.Lock()
        self._count = 0

    def _log(self, label: str) -> Path:
        with self._lock:
            self._count += 1
            slug = re.sub(r"[^a-z0-9]+", "-", label.lower()).strip("-")
            return self.logs / f"{self._count:02d}-{slug[:60]}.log"

    def report(self, result: Result) -> None:
        with self._lock:
            self.results.append(result)
            took = f"{result.seconds:5.0f}s" if result.status != "SKIP" else "      "
            note = f"  ({result.note})" if result.note else ""
            print(f"{result.status:4}  {took}  {result.label}{note}", flush=True)

    def step(self, label: str, command: str | list[str], cwd: Path, path_dirs: list[Path],
             log: Path | None = None) -> bool:
        log = log or self._log(label)
        start = time.monotonic()
        ok = _run(command, cwd, log, path_dirs)
        self.report(Result(label, "PASS" if ok else "FAIL", time.monotonic() - start, log))
        return ok

    def job(self, job: Job, copy: Path, env_steps: list[Step], checks: list[Step]) -> None:
        path_dirs: list[Path] = []
        version = python_version(job)
        problem = ""
        if version:
            env = self.cache / "envs" / job.id
            key = _env_key(copy, version, env_steps)
            stamp = env / STAMP
            if not (stamp.is_file() and stamp.read_text() == key):
                label = f"{job.name} / Set up cached environment"
                log = self._log(label)
                start = time.monotonic()
                shutil.rmtree(env, ignore_errors=True)
                env.parent.mkdir(parents=True, exist_ok=True)
                ok = _make_venv(env, version, log) and all(
                    _run(s.run or "", copy, log, [env / "bin"]) for s in env_steps)
                self.report(Result(label, "PASS" if ok else "FAIL", time.monotonic() - start, log))
                if ok:
                    stamp.write_text(key)
                else:
                    shutil.rmtree(env, ignore_errors=True)
                    problem = "environment setup failed"
            path_dirs = [env / "bin"]
        elif any(s.uses and s.uses.startswith("actions/setup-node") for s in job.steps):
            node = _node_bin(copy)
            if node is None:
                problem = "no Node matching .nvmrc"
                self.report(Result(f"{job.name} / Find Node", "FAIL", note=problem))
            else:
                path_dirs = [node]
        for step in checks:
            label = f"{job.name} / {step.name or (step.run or '').splitlines()[0]}"
            if problem:
                self.report(Result(label, "SKIP", note=problem))
                continue
            if not self.step(label, step.run or "", copy, path_dirs):
                problem = "an earlier step failed"

    def hassfest(self, copy: Path, version: str, note: str) -> None:
        label = f"Hassfest validation (Home Assistant {version})"
        log = self._log(label)
        root = self.cache / "hassfest" / version
        core, venv = root / "core", root / "venv"
        if not (root / STAMP).is_file():
            start = time.monotonic()
            shutil.rmtree(root, ignore_errors=True)
            root.mkdir(parents=True)
            ok = _run(["git", "-c", "advice.detachedHead=false", "clone", "--quiet", "--depth", "1",
                       "--branch", version, CORE_REPO, str(core)], root, log)
            # The image pins a patch release; any build of that minor version validates the same.
            minor = ".".join((core / ".python-version").read_text().strip().split(".")[:2]) if ok else ""
            ok = ok and _make_venv(venv, minor, log)
            if ok:
                ok = _install_hassfest(core, venv, log)
            self.report(Result(f"Hassfest / Set up Home Assistant {version}", "PASS" if ok else "FAIL",
                               time.monotonic() - start, log))
            if not ok:
                shutil.rmtree(root, ignore_errors=True)
                self.report(Result(label, "SKIP", note="setup failed"))
                return
            (root / STAMP).write_text(version)
            for other in (self.cache / "hassfest").iterdir():
                if other != root:
                    shutil.rmtree(other, ignore_errors=True)
        paths: list[str] = []
        for manifest in sorted(copy.glob("custom_components/*/manifest.json")):
            paths += ["--integration-path", str(manifest.parent)]
        start = time.monotonic()
        ok = _run([str(venv / "bin" / "python"), "-m", "script.hassfest", "--action", "validate", *paths],
                  core, log, [venv / "bin"])
        self.report(Result(label, "PASS" if ok else "FAIL", time.monotonic() - start, log, note))


def _pin(path: Path, package: str) -> str:
    match = re.search(rf"^{re.escape(package)}==(\S+)", path.read_text(), re.MULTILINE)
    return f"{package}=={match.group(1)}" if match else package


def _install_hassfest(core: Path, venv: Path, log: Path) -> bool:
    """Install what the hassfest image installs: core requirements, then its tools."""
    python = str(venv / "bin" / "python")
    tools = [_pin(core / "requirements_test.txt", "pipdeptree"), _pin(core / "requirements_test.txt", "tqdm"),
             _pin(core / "requirements_test_pre_commit.txt", "ruff")]
    base = ["-c", "homeassistant/package_constraints.txt", "-r", "requirements.txt"]
    if shutil.which("uv"):
        return (_run(["uv", "pip", "install", "--quiet", "--python", python, *base], core, log)
                and _run(["uv", "pip", "install", "--quiet", "--python", python, *tools], core, log))
    return (_run([python, "-m", "pip", "install", "--quiet", *base], core, log)
            and _run([python, "-m", "pip", "install", "--quiet", *tools], core, log))


def hassfest_version(cache: Path, override: str | None) -> tuple[str | None, str]:
    """The newest published Home Assistant release, which the hassfest image tracks."""
    if override:
        return override, "chosen with --hassfest-version"
    releases = None
    try:
        request = urllib.request.Request(RELEASES_API, headers={
            "Accept": "application/vnd.github+json", "User-Agent": "phoenix-mcp-check-before-push"})
        with urllib.request.urlopen(request, timeout=20) as response:
            releases = json.load(response)
    except Exception:
        if shutil.which("gh"):
            out = subprocess.run(["gh", "api", "repos/home-assistant/core/releases?per_page=10"],
                                 capture_output=True, text=True)
            if out.returncode == 0:
                releases = json.loads(out.stdout)
    if releases:
        published = sorted((r for r in releases if not r.get("draft")),
                           key=lambda r: r.get("published_at") or "", reverse=True)
        if published:
            return published[0]["tag_name"], ""
    cached = sorted((cache / "hassfest").glob(f"*/{STAMP}"), key=lambda p: p.stat().st_mtime)
    if cached:
        return cached[-1].parent.name, "release list unreachable, using the cached release"
    return None, "release list unreachable and nothing cached"


def install_hook(repo: Path, remove: bool) -> int:
    hooks = Path(_git(repo, "rev-parse", "--git-path", "hooks"))
    hook = (repo / hooks if not hooks.is_absolute() else hooks) / "pre-push"
    if hook.exists() and HOOK_MARKER not in hook.read_text():
        print(f"{hook} already exists and was not installed by this script; leaving it alone.")
        return 1
    if remove:
        if hook.exists():
            hook.unlink()
            print(f"Removed {hook}")
        return 0
    hook.parent.mkdir(parents=True, exist_ok=True)
    hook.write_text(HOOK)
    hook.chmod(0o755)
    print(f"Installed {hook}. Every push now runs the quick checks on the pushed commits.")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("ref", nargs="?", default="HEAD", help="commit, branch or tag to check (default HEAD)")
    parser.add_argument("--full", action="store_true", help="also run every pytest step")
    parser.add_argument("--serial", action="store_true", help="run one job at a time")
    parser.add_argument("--cache", help="cache directory (default ~/.cache/phoenix-mcp-checks)")
    parser.add_argument("--hassfest-version", help="Home Assistant release to run hassfest from")
    parser.add_argument("--install-hook", action="store_true", help="run the quick checks on every git push")
    parser.add_argument("--uninstall-hook", action="store_true", help="remove that pre-push hook")
    args = parser.parse_args(argv)

    repo = Path(subprocess.run(["git", "rev-parse", "--show-toplevel"], check=True,
                               capture_output=True, text=True).stdout.strip())
    if args.install_hook or args.uninstall_hook:
        return install_hook(repo, args.uninstall_hook)

    sha = _git(repo, "rev-parse", "--verify", f"{args.ref}^{{commit}}")
    subject = _git(repo, "log", "-1", "--format=%s", sha)
    cache = Path(args.cache or os.environ.get("PHOENIX_CHECK_CACHE") or Path.home() / ".cache" / "phoenix-mcp-checks")
    started = time.strftime("%Y%m%d-%H%M%S")
    run_dir, logs = cache / "runs" / started, cache / "logs" / started
    logs.mkdir(parents=True, exist_ok=True)

    mode = "full" if args.full else "quick"
    print(f"Checking {sha[:7]} {subject} ({mode}, clean copy of the commit)")
    if sha == _git(repo, "rev-parse", "HEAD") and _git(repo, "status", "--porcelain", "--untracked-files=no"):
        print("Note: uncommitted changes are not part of the commit and are not checked.")

    jobs = parse_workflow(_git(repo, "show", f"{sha}:{WORKFLOW}"))
    planned = [(job, *plan(job, args.full)) for job in jobs]
    planned = [p for p in planned if p[2]]
    version, version_note = hassfest_version(cache, args.hassfest_version)

    runner = Runner(cache, logs)
    _git(repo, "worktree", "prune")
    copies: dict[str, Path] = {}
    total = time.monotonic()
    try:
        for name in ["hassfest", *(job.id for job, _, _ in planned)]:
            copies[name] = run_dir / name
            _git(repo, "worktree", "add", "--detach", "--quiet", str(copies[name]), sha)
        tasks = []
        with concurrent.futures.ThreadPoolExecutor(max_workers=1 if args.serial else len(copies)) as pool:
            if version:
                tasks.append(pool.submit(runner.hassfest, copies["hassfest"], version, version_note))
            else:
                runner.report(Result("Hassfest validation", "FAIL", note=version_note))
            for job, env_steps, checks in planned:
                tasks.append(pool.submit(runner.job, job, copies[job.id], env_steps, checks))
            for task in tasks:
                task.result()
    finally:
        for copy in copies.values():
            subprocess.run(["git", "worktree", "remove", "--force", str(copy)], cwd=repo, capture_output=True)
        shutil.rmtree(run_dir, ignore_errors=True)
        subprocess.run(["git", "worktree", "prune"], cwd=repo, capture_output=True)
        for old in sorted((cache / "logs").iterdir())[:-KEEP_LOG_RUNS]:
            shutil.rmtree(old, ignore_errors=True)

    failed = [r for r in runner.results if r.status == "FAIL"]
    for result in failed:
        print(f"\n--- FAIL {result.label}" + (f": {result.note}" if result.note else ""))
        if result.log and result.log.is_file():
            tail = result.log.read_text(errors="replace").splitlines()[-FAILURE_TAIL:]
            print("\n".join(tail))
            print(f"--- full log: {result.log}")
    minutes = (time.monotonic() - total) / 60
    print("\nHACS validation reads the GitHub repository page, so it only runs on GitHub.")
    if not args.full:
        print("Quick mode: pytest steps were not run; use --full for everything GitHub runs.")
    if failed:
        print(f"{len(failed)} check(s) FAILED in {minutes:.1f} min.")
        return 1
    print(f"All {len(runner.results)} checks passed in {minutes:.1f} min.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
