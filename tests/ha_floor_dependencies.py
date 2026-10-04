"""Install Phoenix and imported HA component requirements under HA's constraints.

Run: python tests/ha_floor_dependencies.py [--print]
--print lists the requirements instead of installing them.
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import subprocess
import sys


def main() -> None:
    """Install, or with --print list, the dependency requirements.

    Sources are Phoenix's manifest and the installed HA release's component manifests.
    """
    spec = importlib.util.find_spec("homeassistant")
    if spec is None or spec.origin is None:
        raise RuntimeError("Install the pinned Home Assistant test stack first.")
    ha_root = Path(spec.origin).parent
    root = Path(__file__).resolve().parent.parent
    manifest = json.loads((root / "custom_components/phoenix_mcp/manifest.json").read_text())
    requirements = set(manifest["requirements"])
    domains = set(manifest["after_dependencies"]) | set(manifest["dependencies"]) | {"conversation", "tts", "camera", "local_calendar"}
    for domain in sorted(domains):
        component = json.loads((ha_root / "components" / domain / "manifest.json").read_text())
        requirements.update(component.get("requirements", []))
    if "--print" in sys.argv:
        print("\n".join(sorted(requirements)))
        return
    subprocess.run([
        sys.executable, "-m", "pip", "install", "--constraint", str(ha_root / "package_constraints.txt"),
        *sorted(requirements),
    ], check=True)


if __name__ == "__main__":
    main()
