import io
import unittest
from pathlib import Path
from unittest.mock import patch

from envtest.configuration import (
    CheckGroup,
    CommandCheck,
    EnvironmentConfiguration,
    PathCheck,
)
from envtest.contracts import CommandResult
from envtest.suite import (
    CheckResult,
    print_environment_check,
    run_environment_checks,
)


class FlushTrackingStream(io.StringIO):
    def __init__(self) -> None:
        super().__init__()
        self.flush_count = 0

    def flush(self) -> None:
        self.flush_count += 1
        super().flush()


class SuiteTests(unittest.TestCase):
    def test_reports_each_check_before_starting_the_next_one(self) -> None:
        groups = tuple(
            CheckGroup(identifier, True, None, (), (), (), ())
            for identifier in ("first", "second")
        )
        configuration = EnvironmentConfiguration(groups)
        events = []

        def run_group(group: CheckGroup, root: Path) -> CheckResult:
            events.append("run {}".format(group.identifier))
            return CheckResult(group.identifier, "passed", 0)

        def report(check: CheckResult) -> None:
            events.append("report {}".format(check.identifier))

        with patch("envtest.suite._run_group", side_effect=run_group):
            result = run_environment_checks(configuration, (), Path.cwd(), report)

        self.assertEqual(
            events,
            ["run first", "report first", "run second", "report second"],
        )
        self.assertEqual(
            tuple(check.identifier for check in result.checks),
            ("first", "second"),
        )

    def test_print_environment_check_flushes_output(self) -> None:
        stream = FlushTrackingStream()

        print_environment_check(CheckResult("example", "passed", 0), stream=stream)

        self.assertEqual(stream.getvalue(), "PASS example\n")
        self.assertEqual(stream.flush_count, 1)

    def test_checks_every_executable_in_a_group(self) -> None:
        group = CheckGroup(
            "toolchain",
            True,
            None,
            (),
            (),
            (
                PathCheck("compiler", (), None),
                PathCheck("formatter", (), None),
            ),
            (),
        )

        with patch(
            "envtest.suite.path_candidates",
            side_effect=((Path("/bin/compiler"),), ()),
        ):
            result = run_environment_checks(
                EnvironmentConfiguration((group,)), (), Path.cwd()
            )

        self.assertEqual(result.checks[0].status, "failed")
        self.assertEqual(result.checks[0].issues[0].identifier, "formatter")

    def test_accepts_versions_at_inclusive_constraint_boundaries(self) -> None:
        group = CheckGroup(
            "python",
            True,
            None,
            (),
            (),
            (),
            (
                CommandCheck(
                    "version",
                    "python3 --version",
                    ("^Python", ">=3.9", ">=3.14", "<=3.14.0"),
                ),
            ),
        )

        with patch(
            "envtest.suite.command_result",
            return_value=CommandResult(0, "Python 3.14.0\n", ""),
        ):
            result = run_environment_checks(
                EnvironmentConfiguration((group,)), (), Path.cwd()
            )

        self.assertEqual(result.checks[0].status, "passed")

    def test_reports_versions_outside_constraints(self) -> None:
        group = CheckGroup(
            "python",
            True,
            None,
            (),
            (),
            (),
            (
                CommandCheck(
                    "version",
                    "python3 --version",
                    (">=3.15", "<=3.13"),
                ),
            ),
        )

        with patch(
            "envtest.suite.command_result",
            return_value=CommandResult(0, "Python 3.14.0\n", ""),
        ):
            result = run_environment_checks(
                EnvironmentConfiguration((group,)), (), Path.cwd()
            )

        self.assertEqual(result.checks[0].status, "failed")
        self.assertEqual(
            result.checks[0].issues[0].diagnostic,
            "version 3.14.0 did not satisfy constraint '>=3.15'; "
            "version 3.14.0 did not satisfy constraint '<=3.13'",
        )

    def test_reports_missing_version_for_constraint(self) -> None:
        group = CheckGroup(
            "tool",
            True,
            None,
            (),
            (),
            (),
            (CommandCheck("version", "tool --version", (">=1.0",)),),
        )

        with patch(
            "envtest.suite.command_result",
            return_value=CommandResult(0, "development build\n", ""),
        ):
            result = run_environment_checks(
                EnvironmentConfiguration((group,)), (), Path.cwd()
            )

        self.assertEqual(result.checks[0].status, "failed")
        self.assertIn(
            "did not find a numeric version",
            result.checks[0].issues[0].diagnostic,
        )


if __name__ == "__main__":
    unittest.main()
