# envtest

`envtest` is a small YAML-driven environment integration-test engine.
It checks installed links, files, executable resolution, and shell commands without owning installation or orchestration policy.

Like Dotbot, the repository exposes a generic engine while consumer-owned scripts prescribe which configurations to load and in what order.

## Usage

Run the engine through uv:

```powershell
uv run envtest.py --root C:\path\to\consumer --config C:\path\to\consumer\test.conf.yaml
```

Configuration files may be repeated.
Later mappings merge recursively and lists replace earlier lists.

```bash
uv run envtest.py \
  --root /path/to/consumer \
  --config /path/to/consumer/test.conf.yaml \
  --config /path/to/consumer/test.unix.conf.yaml
```

Use `--check <identifier>` repeatedly to select named groups, `--list-checks` to inspect the resolved contract, and `--verbose` for detailed diagnostics and unexpected tracebacks.

## Consumer integration

A consumer may add `envtest` as a submodule and keep orchestration in its own launchers:

```powershell
uv run "$PSScriptRoot\envtest\envtest.py" `
  --root $PSScriptRoot `
  --config "$PSScriptRoot\test.conf.yaml" `
  --config "$PSScriptRoot\test.windows.conf.yaml" `
  @args
```

The engine has no platform or profile model.
Separate launchers choose the appropriate configuration files for each installation recipe.

## Configuration

Each check group may use four primitives:

| Field | Purpose |
| --- | --- |
| `links` | Map installed targets to source paths relative to `--root`. |
| `files` | Map required paths to `file` or `directory`. |
| `path` | Require a command on `PATH` and optionally constrain its selected location or candidate count. |
| `commands` | Run named native-shell commands and optionally match output with positive or `!`-prefixed negative regular expressions. |

`if` conditionally skips a group when its native-shell command exits nonzero.
`enabled: false` disables a group through a later configuration overlay.

```yaml
schema_version: 2

checks:
  git:
    links:
      "~/.config/git": config/git
    path: [git]
    commands:
      version: [git --version, git version]

  fzf:
    path: [fzf, "${XDG_DATA_HOME:-$HOME/.local/share}/fzf/bin"]

  powershell:
    path:
      command: pwsh
      locations: ["%ProgramFiles%/WindowsApps"]
      candidates: 2
```

Commands and `if` conditions run through Bash on Unix and PowerShell on Windows.
The child shell inherits the caller environment, does not reload profiles, and runs with `--root` as its working directory.
Captured command output is used only for regex evaluation and is never printed.

The default renderer prints one color-coded status line for every group, expands failures to the failed primitive names, and ends with an aggregate summary.
Set `NO_COLOR` to disable terminal colors.

## Self-check

The repository's own visible contract exercises the engine:

```text
uv run envtest.py --config test.conf.yaml
```
