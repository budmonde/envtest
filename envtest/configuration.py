import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, Mapping, Optional, Tuple

import yaml


CONFIGURATION_SCHEMA_VERSION = 3
CHECK_PRIMITIVES = {"links", "files", "paths", "commands"}


class ConfigurationError(ValueError):
    pass


@dataclass(frozen=True)
class LinkCheck:
    target: str
    source: str


@dataclass(frozen=True)
class FileCheck:
    path: str
    kind: str


@dataclass(frozen=True)
class PathCheck:
    command: str
    expected_locations: Tuple[str, ...]
    candidate_count: Optional[int]


@dataclass(frozen=True)
class CommandCheck:
    identifier: str
    command: str
    patterns: Tuple[str, ...]
    timeout_seconds: Optional[int] = None


@dataclass(frozen=True)
class CheckGroup:
    identifier: str
    enabled: bool
    condition: Optional[str]
    links: Tuple[LinkCheck, ...]
    files: Tuple[FileCheck, ...]
    paths: Tuple[PathCheck, ...]
    commands: Tuple[CommandCheck, ...]


@dataclass(frozen=True)
class EnvironmentConfiguration:
    checks: Tuple[CheckGroup, ...]

    def active_checks(self) -> Tuple[CheckGroup, ...]:
        return tuple(group for group in self.checks if group.enabled)

    def select_checks(self, identifiers: Iterable[str]) -> Tuple[CheckGroup, ...]:
        requested = set(identifiers)
        known = {group.identifier for group in self.checks}
        unknown = sorted(requested.difference(known))
        if unknown:
            raise ConfigurationError("unknown check: {}".format(", ".join(unknown)))
        return tuple(group for group in self.checks if group.identifier in requested)


def load_configuration(config_paths: Iterable[Path]) -> EnvironmentConfiguration:
    merged: Dict[str, Any] = {}

    for config_path in config_paths:
        path = Path(config_path).resolve()
        document = _read_document(path)
        _known_keys(
            document,
            {"schema_version", "checks"},
            str(path),
        )
        if document.get("schema_version") != CONFIGURATION_SCHEMA_VERSION:
            raise ConfigurationError(
                "{}: schema_version must be {}".format(
                    path, CONFIGURATION_SCHEMA_VERSION
                )
            )
        checks = _mapping(document.get("checks"), "{}: checks".format(path))
        for identifier, check in checks.items():
            _nonempty_string(identifier, "{}: check identifier".format(path))
            _mapping(check, "{}: checks.{}".format(path, identifier))
        document = _normalize_paths(document, str(path))
        merged = _merge(merged, document)

    return EnvironmentConfiguration(
        checks=tuple(
            _parse_group(identifier, value)
            for identifier, value in _mapping(merged.get("checks"), "checks").items()
        ),
    )


def _read_document(path: Path) -> Dict[str, Any]:
    try:
        document = yaml.safe_load(path.read_text(encoding="utf-8"))
    except OSError as error:
        raise ConfigurationError(
            "{}: cannot read configuration: {}".format(path, error)
        ) from error
    except yaml.YAMLError as error:
        raise ConfigurationError("{}: invalid YAML: {}".format(path, error)) from error
    if not isinstance(document, dict):
        raise ConfigurationError("{}: configuration root must be a mapping".format(path))
    return document


def _parse_group(identifier: str, value: Any) -> CheckGroup:
    prefix = "checks.{}".format(identifier)
    group = _mapping(value, prefix)
    _known_keys(group, CHECK_PRIMITIVES | {"enabled", "if"}, prefix)
    enabled = group.get("enabled", True)
    if type(enabled) is not bool:
        raise ConfigurationError("{}.enabled must be a boolean".format(prefix))
    if not enabled:
        return CheckGroup(identifier, False, None, (), (), (), ())

    condition = _optional_string(group, "if", "{}.if".format(prefix))
    links = _parse_links(group.get("links"), prefix)
    files = _parse_files(group.get("files"), prefix)
    paths = _parse_paths(group.get("paths"), prefix)
    commands = _parse_commands(group.get("commands"), prefix)
    if not links and not files and not paths and not commands:
        raise ConfigurationError(
            "{} must declare at least one nonempty primitive".format(prefix)
        )
    return CheckGroup(identifier, True, condition, links, files, paths, commands)


def _parse_links(value: Any, prefix: str) -> Tuple[LinkCheck, ...]:
    links = _mapping(value, "{}.links".format(prefix))
    return tuple(
        LinkCheck(
            _nonempty_string(target, "{}.links target".format(prefix)),
            _nonempty_string(source, "{}.links.{}".format(prefix, target)),
        )
        for target, source in links.items()
    )


def _parse_files(value: Any, prefix: str) -> Tuple[FileCheck, ...]:
    files = _mapping(value, "{}.files".format(prefix))
    parsed = []
    for path, kind in files.items():
        filename = _nonempty_string(path, "{}.files path".format(prefix))
        if kind not in {"file", "directory"}:
            raise ConfigurationError(
                "{}.files.{} must be 'file' or 'directory'".format(prefix, path)
            )
        parsed.append(FileCheck(filename, kind))
    return tuple(parsed)


def _normalize_paths(document: Dict[str, Any], prefix: str) -> Dict[str, Any]:
    normalized = dict(document)
    checks = dict(_mapping(document.get("checks"), "{}: checks".format(prefix)))
    normalized["checks"] = checks
    for identifier, value in checks.items():
        group = dict(_mapping(value, "{}: checks.{}".format(prefix, identifier)))
        checks[identifier] = group
        paths = group.get("paths")
        if not isinstance(paths, list):
            continue
        name = "{}: checks.{}.paths".format(prefix, identifier)
        commands = _string_list(paths, name)
        if len(commands) != len(set(commands)):
            raise ConfigurationError("{} must not contain duplicate commands".format(name))
        group["paths"] = {command: {} for command in commands}
    return normalized


def _parse_paths(value: Any, prefix: str) -> Tuple[PathCheck, ...]:
    paths = _mapping(value, "{}.paths".format(prefix))
    parsed = []
    for command, value in paths.items():
        name = "{}.paths.{}".format(prefix, command)
        executable = _nonempty_string(command, "{} command".format(name))
        if not isinstance(value, dict):
            raise ConfigurationError("{} must be a mapping".format(name))
        _known_keys(value, {"locations", "candidate_count"}, name)
        locations = _locations(value.get("locations"), name)
        candidate_count = value.get("candidate_count")
        if candidate_count is not None and (
            type(candidate_count) is not int or candidate_count < 1
        ):
            raise ConfigurationError(
                "{}.candidate_count must be a positive integer".format(name)
            )
        parsed.append(PathCheck(executable, locations, candidate_count))
    return tuple(parsed)


def _locations(value: Any, prefix: str) -> Tuple[str, ...]:
    if value is None:
        return ()
    values = [value] if isinstance(value, str) else value
    return _string_list(values, "{}.locations".format(prefix), require=True)


def _parse_commands(value: Any, prefix: str) -> Tuple[CommandCheck, ...]:
    commands = _mapping(value, "{}.commands".format(prefix))
    parsed = []
    for identifier, value in commands.items():
        name = "{}.commands.{}".format(prefix, identifier)
        _nonempty_string(identifier, "{} identifier".format(name))
        if isinstance(value, list):
            parts = _string_list(value, name, require=True)
            command, patterns, timeout = parts[0], parts[1:], None
        elif isinstance(value, dict):
            _known_keys(value, {"command", "patterns", "timeout"}, name)
            command = _nonempty_string(value.get("command"), "{}.command".format(name))
            patterns = _string_list(
                value.get("patterns", []), "{}.patterns".format(name)
            )
            timeout = value.get("timeout")
            if timeout is not None and (type(timeout) is not int or timeout < 1):
                raise ConfigurationError(
                    "{}.timeout must be a positive integer".format(name)
                )
        else:
            raise ConfigurationError("{} must be a list or mapping".format(name))
        _validate_patterns(patterns, name)
        parsed.append(CommandCheck(identifier, command, patterns, timeout))
    return tuple(parsed)


def _validate_patterns(patterns: Tuple[str, ...], name: str) -> None:
    for pattern in patterns:
        expression = pattern[1:] if pattern.startswith("!") else pattern
        if not expression:
            raise ConfigurationError("{} regex pattern must not be empty".format(name))
        try:
            re.compile(expression)
        except re.error as error:
            raise ConfigurationError(
                "{} has an invalid regex: {}".format(name, error)
            ) from error


def _mapping(value: Any, name: str) -> Mapping[str, Any]:
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise ConfigurationError("{} must be a mapping".format(name))
    return value


def _known_keys(value: Mapping[str, Any], allowed: set, prefix: str) -> None:
    unknown = next((key for key in value if key not in allowed), None)
    if unknown is not None:
        raise ConfigurationError("{}: unknown key '{}'".format(prefix, unknown))


def _nonempty_string(value: Any, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ConfigurationError("{} must be a nonempty string".format(name))
    return value


def _string_list(value: Any, name: str, require: bool = False) -> Tuple[str, ...]:
    if not isinstance(value, list) or (require and not value):
        qualifier = "nonempty " if require else ""
        raise ConfigurationError("{} must be a {}list".format(name, qualifier))
    return tuple(_nonempty_string(item, name) for item in value)


def _optional_string(
    section: Mapping[str, Any], key: str, name: str
) -> Optional[str]:
    value = section.get(key)
    return None if value is None else _nonempty_string(value, name)


def _merge(left: Dict[str, Any], right: Mapping[str, Any]) -> Dict[str, Any]:
    merged = dict(left)
    for key, value in right.items():
        existing = merged.get(key)
        merged[key] = (
            _merge(existing, value)
            if isinstance(existing, dict) and isinstance(value, dict)
            else value
        )
    return merged
