import os
import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional, Tuple

from envtest.configuration import CommandCheck, FileCheck, LinkCheck


_POSIX_DEFAULT = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*):-([^}]*)\}")
COMMAND_TIMEOUT_SECONDS = 10


@dataclass(frozen=True)
class CommandResult:
    returncode: int
    stdout: str
    stderr: str


def source_path(contract: LinkCheck, root: Path) -> Path:
    return (root / contract.source).resolve()


def resolved_link_target(contract: LinkCheck) -> Optional[Path]:
    installed_path = expand_path(contract.target)
    if not installed_path.exists():
        return None
    return installed_path.resolve()


def required_file_path(contract: FileCheck, root: Path) -> Path:
    path = expand_path(contract.path)
    return path if path.is_absolute() else root / path


def path_candidates(command: str) -> Tuple[Path, ...]:
    selected = shutil.which(command)
    candidates = []
    if selected:
        candidates.append(_absolute_path(Path(selected)))

    command_path = Path(command)
    if command_path.parent != Path("."):
        _append_candidate(candidates, command_path)
        return tuple(candidates)

    for entry in os.environ.get("PATH", "").split(os.pathsep):
        if not entry:
            continue
        for suffix in _command_suffixes(command):
            _append_candidate(candidates, Path(entry) / "{}{}".format(command, suffix))
    return tuple(candidates)


def command_result(contract: CommandCheck, root: Path) -> CommandResult:
    return shell_command_result(contract.command, root, contract.timeout_seconds)


def shell_command_result(
    command: str,
    root: Path,
    timeout_seconds: Optional[int] = None,
) -> CommandResult:
    try:
        result = subprocess.run(
            command_argv(command),
            cwd=str(root),
            capture_output=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout_seconds or COMMAND_TIMEOUT_SECONDS,
            check=False,
        )
    except OSError as error:
        return CommandResult(returncode=-1, stdout="", stderr=str(error))
    except subprocess.TimeoutExpired:
        return CommandResult(returncode=-1, stdout="", stderr="command timed out")
    return CommandResult(result.returncode, result.stdout, result.stderr)


def command_argv(command: str) -> Tuple[str, ...]:
    if os.name == "nt":
        return ("pwsh", "-NoProfile", "-NonInteractive", "-Command", command)
    return ("bash", "-c", command)


def expand_path(value: str) -> Path:
    expanded = value
    for _ in range(2):
        expanded = _POSIX_DEFAULT.sub(_expand_posix_default, expanded)
        expanded = os.path.expandvars(expanded)
    return Path(expanded).expanduser()


def _append_candidate(candidates: List[Path], path: Path) -> None:
    if not path.is_file() or not os.access(str(path), os.X_OK):
        return
    absolute = _absolute_path(path)
    if all(not _same_path(absolute, candidate) for candidate in candidates):
        candidates.append(absolute)


def _absolute_path(path: Path) -> Path:
    return Path(os.path.abspath(str(path)))


def _same_path(left: Path, right: Path) -> bool:
    return os.path.normcase(str(left)) == os.path.normcase(str(right))


def path_is_within(candidate: Path, location: Path) -> bool:
    try:
        common = os.path.commonpath(
            (os.path.normcase(str(candidate)), os.path.normcase(str(location)))
        )
    except ValueError:
        return False
    return common == os.path.normcase(str(location))


def _expand_posix_default(match: re.Match) -> str:
    name, default = match.groups()
    return os.environ.get(name) or default


def _command_suffixes(command: str) -> Tuple[str, ...]:
    if os.name != "nt" or Path(command).suffix:
        return ("",)
    extensions = os.environ.get("PATHEXT", ".COM;.EXE;.BAT;.CMD").split(";")
    return tuple(extension.lower() for extension in extensions)
