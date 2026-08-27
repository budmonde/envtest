import json
import os
import re
import subprocess
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, Mapping, Optional

from envtest import __version__
from envtest.suite import CheckIssue, SuiteResult


RESULT_LOG_SCHEMA_VERSION = 1
LOG_DIRECTORY_ENVIRONMENT_VARIABLE = "ENVTEST_LOG_DIRECTORY"
MACHINE_ID_ENVIRONMENT_VARIABLE = "ENVTEST_MACHINE_ID"
PATH_SEGMENT_PATTERN = re.compile(r"[A-Za-z0-9](?:[A-Za-z0-9._-]*[A-Za-z0-9])?")


class ResultLogError(RuntimeError):
    pass


def append_result_change(
    result: SuiteResult,
    root: Path,
    suite: str,
    selected_identifiers: Iterable[str] = (),
    environment: Optional[Mapping[str, str]] = None,
    observed_at: Optional[str] = None,
) -> Optional[Path]:
    values = os.environ if environment is None else environment
    machine_id = _optional_environment_value(values, MACHINE_ID_ENVIRONMENT_VARIABLE)
    log_directory = _optional_environment_value(
        values, LOG_DIRECTORY_ENVIRONMENT_VARIABLE
    )
    if machine_id is None or log_directory is None or tuple(selected_identifiers):
        return None

    machine_id = _path_segment(machine_id, MACHINE_ID_ENVIRONMENT_VARIABLE)
    suite = _path_segment(suite.strip(), "suite")
    repository = _repository_remote_name(root)
    path = (
        Path(os.path.expandvars(log_directory)).expanduser()
        / machine_id
        / repository
        / "{}.jsonl".format(suite)
    )
    entry = _entry(result, machine_id, repository, suite, observed_at)
    _append_if_changed(path, entry)
    return path


def _optional_environment_value(
    environment: Mapping[str, str], name: str
) -> Optional[str]:
    value = environment.get(name)
    if value is None or not value.strip():
        return None
    return value.strip()


def _path_segment(value: str, name: str) -> str:
    if PATH_SEGMENT_PATTERN.fullmatch(value) is None:
        raise ResultLogError(
            "{} must contain only letters, numbers, '.', '_', or '-'".format(name)
        )
    return value


def _repository_remote_name(root: Path) -> str:
    try:
        process = subprocess.run(
            ["git", "config", "--get", "remote.origin.url"],
            cwd=str(root),
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            encoding="utf-8",
            errors="replace",
            timeout=5,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        raise ResultLogError(
            "cannot read the consumer repository's origin remote"
        ) from error
    if process.returncode != 0 or not process.stdout.strip():
        raise ResultLogError("consumer repository has no origin remote")

    remote = process.stdout.strip().rstrip("/\\")
    name = re.split(r"[/\\:]", remote)[-1]
    if name.endswith(".git"):
        name = name[:-4]
    return _path_segment(name, "origin remote name")


def _entry(
    result: SuiteResult,
    machine_id: str,
    repository: str,
    suite: str,
    observed_at: Optional[str],
) -> Dict[str, Any]:
    counts = {
        status: sum(check.status == status for check in result.checks)
        for status in ("passed", "failed", "error", "skipped")
    }
    return {
        "schema_version": RESULT_LOG_SCHEMA_VERSION,
        "observed_at": observed_at or _utc_timestamp(),
        "producer": {"name": "envtest", "version": __version__},
        "machine_id": machine_id,
        "repository": repository,
        "suite": suite,
        "result": {
            "successful": result.successful,
            "counts": counts,
            "duration_seconds": round(result.duration_seconds, 6),
            "checks": [
                {
                    "identifier": check.identifier,
                    "status": check.status,
                    "duration_seconds": round(check.duration_seconds, 6),
                    "issues": [_issue(issue) for issue in check.issues],
                    "warnings": [_issue(warning) for warning in check.warnings],
                }
                for check in result.checks
            ],
        },
    }


def _issue(issue: CheckIssue) -> Dict[str, str]:
    return {
        "primitive": issue.primitive,
        "identifier": issue.identifier,
        "diagnostic": issue.diagnostic,
    }


def _utc_timestamp() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace(
        "+00:00", "Z"
    )


def _append_if_changed(path: Path, entry: Mapping[str, Any]) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        existing = path.read_text(encoding="utf-8") if path.exists() else ""
        previous = _last_entry(existing, path)
        if previous is not None and _result_state(previous) == _result_state(entry):
            return
        _replace_with_appended_entry(path, existing, entry)
    except ResultLogError:
        raise
    except (OSError, UnicodeError) as error:
        raise ResultLogError("cannot update result log {}".format(path)) from error


def _last_entry(existing: str, path: Path) -> Optional[Mapping[str, Any]]:
    lines = [line for line in existing.splitlines() if line.strip()]
    if not lines:
        return None
    try:
        entry = json.loads(lines[-1])
    except json.JSONDecodeError as error:
        raise ResultLogError(
            "result log has an invalid final JSON line: {}".format(path)
        ) from error
    if not isinstance(entry, dict):
        raise ResultLogError(
            "result log has a non-object final JSON line: {}".format(path)
        )
    return entry


def _result_state(entry: Mapping[str, Any]) -> Any:
    result = entry.get("result")
    if not isinstance(result, dict):
        return None
    checks = result.get("checks")
    if not isinstance(checks, list):
        return None
    return {
        "successful": result.get("successful"),
        "counts": result.get("counts"),
        "checks": [
            {
                key: value
                for key, value in check.items()
                if key != "duration_seconds"
            }
            if isinstance(check, dict)
            else check
            for check in checks
        ],
    }


def _replace_with_appended_entry(
    path: Path, existing: str, entry: Mapping[str, Any]
) -> None:
    descriptor, temporary_name = tempfile.mkstemp(
        dir=str(path.parent), prefix=".{}-".format(path.name), suffix=".tmp"
    )
    temporary_path = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as stream:
            stream.write(existing)
            if existing and not existing.endswith(("\n", "\r")):
                stream.write("\n")
            stream.write(
                json.dumps(
                    entry,
                    ensure_ascii=False,
                    separators=(",", ":"),
                    sort_keys=True,
                )
            )
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(str(temporary_path), str(path))
    finally:
        if temporary_path.exists():
            temporary_path.unlink()
