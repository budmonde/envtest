# /// script
# requires-python = ">=3.8"
# dependencies = ["PyYAML>=6,<7"]
# ///

import argparse
import sys
from pathlib import Path
from typing import Optional, Sequence


PROJECT_ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(PROJECT_ROOT))

from envtest.configuration import ConfigurationError, load_configuration
from envtest.publication import ResultLogError, append_result_change
from envtest.suite import print_environment_result, run_environment_checks


def parse_args(arguments: Optional[Sequence[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run YAML-defined environment checks."
    )
    parser.add_argument(
        "-c",
        "--config",
        action="append",
        required=True,
        type=Path,
        help="YAML configuration file; later files override earlier files.",
    )
    parser.add_argument(
        "-r",
        "--root",
        type=Path,
        default=Path.cwd(),
        help="Consumer root for relative sources, files, and command execution.",
    )
    parser.add_argument(
        "--check",
        action="append",
        default=[],
        metavar="IDENTIFIER",
        help="Run only this named YAML check group; may be repeated.",
    )
    parser.add_argument(
        "--list-checks",
        action="store_true",
        help="List resolved YAML check groups without running them.",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Show detailed diagnostics, durations, and unexpected tracebacks.",
    )
    parser.add_argument(
        "--suite",
        default="default",
        metavar="IDENTIFIER",
        help="Logical suite name used for optional result change logging.",
    )
    return parser.parse_args(arguments)


def main(arguments: Optional[Sequence[str]] = None) -> int:
    args = parse_args(arguments)
    try:
        configuration = load_configuration(args.config)
        selected = tuple(args.check)
        groups = (
            configuration.select_checks(selected)
            if selected
            else configuration.checks
        )
    except ConfigurationError as error:
        print("configuration error: {}".format(error), file=sys.stderr)
        return 2

    if args.list_checks:
        for group in groups:
            states = ["active" if group.enabled else "inactive"]
            if group.enabled and group.condition is not None:
                states.append("conditional")
            print("{} ({})".format(group.identifier, ", ".join(states)))
        return 0

    root = args.root.resolve()
    result = run_environment_checks(configuration, selected, root)
    print_environment_result(result, verbose=args.verbose)
    try:
        append_result_change(result, root, args.suite, selected)
    except ResultLogError as error:
        print("result log error: {}".format(error), file=sys.stderr)
    return 0 if result.successful else 1


if __name__ == "__main__":
    raise SystemExit(main())
