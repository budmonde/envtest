import os
import re
import sys
import time
import traceback
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable, List, Optional, TextIO, Tuple

from envtest.configuration import CheckGroup, EnvironmentConfiguration
from envtest.contracts import (
    command_result,
    expand_path,
    path_candidates,
    path_is_within,
    required_file_path,
    resolved_link_target,
    shell_command_result,
    source_path,
)


@dataclass(frozen=True)
class CheckIssue:
    primitive: str
    identifier: str
    diagnostic: str


@dataclass(frozen=True)
class CheckResult:
    identifier: str
    status: str
    duration_seconds: float
    issues: Tuple[CheckIssue, ...] = ()
    warnings: Tuple[CheckIssue, ...] = ()
    traceback_text: Optional[str] = None


@dataclass(frozen=True)
class SuiteResult:
    checks: Tuple[CheckResult, ...]
    duration_seconds: float

    @property
    def successful(self) -> bool:
        return all(check.status not in {"failed", "error"} for check in self.checks)


def run_environment_checks(
    configuration: EnvironmentConfiguration,
    selected_identifiers: Iterable[str],
    root: Path,
    on_check_complete: Optional[Callable[[CheckResult], None]] = None,
) -> SuiteResult:
    identifiers = tuple(selected_identifiers)
    groups = (
        configuration.select_checks(identifiers)
        if identifiers
        else configuration.active_checks()
    )
    started = time.perf_counter()
    checks = []
    for group in groups:
        check = _run_group(group, root)
        checks.append(check)
        if on_check_complete is not None:
            on_check_complete(check)
    return SuiteResult(tuple(checks), time.perf_counter() - started)


def _run_group(group: CheckGroup, root: Path) -> CheckResult:
    started = time.perf_counter()
    if not group.enabled:
        return CheckResult(group.identifier, "skipped", 0, _diagnostic_issue("disabled"))
    if group.condition is not None:
        condition = shell_command_result(group.condition, root)
        if condition.returncode != 0:
            return CheckResult(
                group.identifier,
                "skipped",
                time.perf_counter() - started,
                _diagnostic_issue("if condition returned a nonzero exit code"),
            )

    issues: List[CheckIssue] = []
    notices: List[CheckIssue] = []
    try:
        _check_links(group, root, issues)
        _check_files(group, root, issues)
        _check_path(group, issues, notices)
        _check_commands(group, root, issues)
    except Exception as error:
        return CheckResult(
            group.identifier,
            "error",
            time.perf_counter() - started,
            _diagnostic_issue("unexpected {}: {}".format(type(error).__name__, error)),
            tuple(notices),
            traceback.format_exc(),
        )
    return CheckResult(
        group.identifier,
        "failed" if issues else "passed",
        time.perf_counter() - started,
        tuple(issues),
        tuple(notices),
    )


def _diagnostic_issue(message: str) -> Tuple[CheckIssue, ...]:
    return (CheckIssue("check", "", message),)


def _check_links(
    group: CheckGroup, root: Path, issues: List[CheckIssue]
) -> None:
    for contract in group.links:
        actual = resolved_link_target(contract)
        expected = source_path(contract, root)
        if actual != expected:
            issues.append(
                CheckIssue(
                    "link",
                    contract.target,
                    "resolved to {}; expected {}".format(actual or "missing", expected),
                )
            )


def _check_files(
    group: CheckGroup, root: Path, issues: List[CheckIssue]
) -> None:
    for contract in group.files:
        path = required_file_path(contract, root)
        if not path.exists():
            issues.append(CheckIssue("file", contract.path, "required path is missing"))
        elif contract.kind == "file" and not path.is_file():
            issues.append(CheckIssue("file", contract.path, "required path is not a file"))
        elif contract.kind == "directory" and not path.is_dir():
            issues.append(
                CheckIssue("file", contract.path, "required path is not a directory")
            )


def _check_path(
    group: CheckGroup,
    issues: List[CheckIssue],
    notices: List[CheckIssue],
) -> None:
    contract = group.path
    if contract is None:
        return
    issue_count = len(issues)
    candidates = path_candidates(contract.command)
    if not candidates:
        issues.append(
            CheckIssue("PATH", contract.command, "command does not resolve from PATH")
        )
        return
    if contract.expected_locations:
        expected_locations = tuple(
            expand_path(location).absolute() for location in contract.expected_locations
        )
        if not any(
            path_is_within(candidates[0], location) for location in expected_locations
        ):
            issues.append(
                CheckIssue(
                    "PATH",
                    contract.command,
                    "selected {}; expected a path below one of {}".format(
                        candidates[0], ", ".join(str(path) for path in expected_locations)
                    ),
                )
            )
    candidate_list = ", ".join(str(candidate) for candidate in candidates)
    if contract.candidate_count is not None and len(candidates) != contract.candidate_count:
        issues.append(
            CheckIssue(
                "PATH",
                contract.command,
                "found {} candidates instead of {}: {}".format(
                    len(candidates), contract.candidate_count, candidate_list
                ),
            )
        )
    elif (
        contract.candidate_count is None
        and len(candidates) > 1
        and len(issues) == issue_count
    ):
        notices.append(
            CheckIssue(
                "PATH",
                contract.command,
                "found {} candidates; selected {}. Candidates: {}".format(
                    len(candidates), candidates[0], candidate_list
                ),
            )
        )


def _check_commands(
    group: CheckGroup, root: Path, issues: List[CheckIssue]
) -> None:
    for contract in group.commands:
        result = command_result(contract, root)
        if result.returncode != 0:
            issues.append(
                CheckIssue(
                    "command",
                    contract.identifier,
                    "returned exit code {}".format(result.returncode),
                )
            )
            continue
        output = "{}\n{}".format(result.stdout, result.stderr)
        problems = []
        for pattern in contract.patterns:
            expression = pattern[1:] if pattern.startswith("!") else pattern
            matched = re.search(expression, output) is not None
            if pattern.startswith("!") and matched:
                problems.append("matched prohibited regex {!r}".format(expression))
            elif not pattern.startswith("!") and not matched:
                problems.append("did not match required regex {!r}".format(expression))
        if problems:
            issues.append(
                CheckIssue("command", contract.identifier, "; ".join(problems))
            )


def print_environment_result(
    result: SuiteResult,
    verbose: bool = False,
    stream: Optional[TextIO] = None,
) -> None:
    for check in result.checks:
        print_environment_check(check, verbose, stream)
    print_environment_summary(result, stream)


def print_environment_check(
    check: CheckResult,
    verbose: bool = False,
    stream: Optional[TextIO] = None,
) -> None:
    output = stream or sys.stdout
    color = _uses_color(output)
    _print_check(output, check, color, verbose)
    if check.warnings:
        _print_warnings(output, check, color, verbose)
    output.flush()


def print_environment_summary(
    result: SuiteResult,
    stream: Optional[TextIO] = None,
) -> None:
    output = stream or sys.stdout
    color = _uses_color(output)
    if result.checks:
        print(file=output)
    print(_summary(result, color), file=output, flush=True)


def _print_check(
    stream: TextIO, check: CheckResult, color: bool, verbose: bool
) -> None:
    label, code = {
        "passed": ("PASS", "32"),
        "failed": ("FAIL", "31"),
        "error": ("ERROR", "31;1"),
        "skipped": ("SKIP", "36"),
    }[check.status]
    duration = " ({:.2f}s)".format(check.duration_seconds) if verbose else ""
    print("{} {}{}".format(_paint(label, code, color), check.identifier, duration), file=stream)
    for issue in check.issues:
        if issue.identifier:
            print("  {}: {}".format(issue.primitive, issue.identifier), file=stream)
        if verbose:
            print("    {}".format(issue.diagnostic), file=stream)
    if verbose and check.traceback_text:
        for line in check.traceback_text.rstrip().splitlines():
            print("    {}".format(line), file=stream)


def _print_warnings(
    stream: TextIO, check: CheckResult, color: bool, verbose: bool
) -> None:
    print("{} {}".format(_paint("WARN", "33", color), check.identifier), file=stream)
    for warning in check.warnings:
        print("  {}: {}".format(warning.primitive, warning.identifier), file=stream)
        if verbose:
            print("    {}".format(warning.diagnostic), file=stream)


def _summary(result: SuiteResult, color: bool) -> str:
    counts = {
        status: sum(check.status == status for check in result.checks)
        for status in ("passed", "failed", "error", "skipped")
    }
    parts = [_paint("{} passed".format(counts["passed"]), "32", color)]
    if counts["failed"]:
        parts.append(_paint("{} failed".format(counts["failed"]), "31", color))
    if counts["error"]:
        parts.append(_paint("{} errors".format(counts["error"]), "31;1", color))
    if counts["skipped"]:
        parts.append(_paint("{} skipped".format(counts["skipped"]), "36", color))
    return "{}, {} checks in {:.2f}s".format(
        ", ".join(parts), len(result.checks), result.duration_seconds
    )


def _uses_color(stream: TextIO) -> bool:
    return bool(
        "NO_COLOR" not in os.environ
        and os.environ.get("TERM") != "dumb"
        and getattr(stream, "isatty", lambda: False)()
    )


def _paint(value: str, code: str, enabled: bool) -> str:
    return "\033[{}m{}\033[0m".format(code, value) if enabled else value
