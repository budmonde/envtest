import io
import unittest
from pathlib import Path
from unittest.mock import patch

from envtest.configuration import CheckGroup, EnvironmentConfiguration, PathCheck
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


if __name__ == "__main__":
    unittest.main()
