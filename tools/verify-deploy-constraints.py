"""Verify every VM deploy requirement has one compatible exact constraint."""

from __future__ import annotations

from pathlib import Path

from packaging.requirements import Requirement
from packaging.utils import canonicalize_name


ROOT = Path(__file__).resolve().parent.parent
CONSTRAINTS = ROOT / "constraints-deploy.txt"
REQUIREMENTS = (
    ROOT / "requirements-core.txt",
    ROOT / "requirements-integrations.txt",
)


def _requirement_lines(path: Path):
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if line and not line.startswith(("#", "-r")):
            yield Requirement(line)


def main() -> int:
    pins: dict[str, Requirement] = {}
    for requirement in _requirement_lines(CONSTRAINTS):
        name = canonicalize_name(requirement.name)
        exact = [item for item in requirement.specifier if item.operator == "=="]
        if len(exact) != 1 or len(list(requirement.specifier)) != 1:
            raise SystemExit(f"constraint must be one exact pin: {requirement}")
        if name in pins:
            raise SystemExit(f"duplicate constraint: {name}")
        pins[name] = requirement

    missing = []
    incompatible = []
    for path in REQUIREMENTS:
        for requirement in _requirement_lines(path):
            name = canonicalize_name(requirement.name)
            pin = pins.get(name)
            if pin is None:
                missing.append(f"{path.name}:{requirement}")
                continue
            version = next(iter(pin.specifier)).version
            if version not in requirement.specifier:
                incompatible.append(f"{requirement} vs {pin}")

    if missing or incompatible:
        details = [*(f"missing {item}" for item in missing), *(f"incompatible {item}" for item in incompatible)]
        raise SystemExit("\n".join(details))
    print(f"deploy constraints verified: {len(pins)} exact pins")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
