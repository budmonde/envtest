import re
from typing import Optional, Tuple


Version = Tuple[int, ...]
VersionConstraint = Tuple[str, Version]

_CONSTRAINT_PREFIX = re.compile(r"^(?:>=|<=)")
_CONSTRAINT = re.compile(r"^(>=|<=)\s*(\d+(?:\.\d+)*)$")
_OUTPUT_VERSION = re.compile(
    r"(?<![A-Za-z0-9])v?(\d+(?:\.\d+)*)(?![A-Za-z0-9])"
)


def parse_version_constraint(value: str) -> Optional[VersionConstraint]:
    if _CONSTRAINT_PREFIX.match(value) is None:
        return None
    match = _CONSTRAINT.fullmatch(value)
    if match is None:
        raise ValueError("expected >= or <= followed by a numeric version")
    operator, version = match.groups()
    return operator, _parse_version(version)


def find_version(value: str) -> Optional[Tuple[str, Version]]:
    match = _OUTPUT_VERSION.search(value)
    if match is None:
        return None
    text = match.group(1)
    return text, _parse_version(text)


def satisfies(version: Version, constraint: VersionConstraint) -> bool:
    operator, expected = constraint
    width = max(len(version), len(expected))
    actual_parts = version + (0,) * (width - len(version))
    expected_parts = expected + (0,) * (width - len(expected))
    if operator == ">=":
        return actual_parts >= expected_parts
    return actual_parts <= expected_parts


def _parse_version(value: str) -> Version:
    return tuple(int(part) for part in value.split("."))
