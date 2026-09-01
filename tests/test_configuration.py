import tempfile
import unittest
from pathlib import Path

from envtest.configuration import ConfigurationError, load_configuration


class ConfigurationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def load(self, *documents: str):
        paths = []
        for index, document in enumerate(documents):
            path = self.root / "{}.yaml".format(index)
            path.write_text(document, encoding="utf-8")
            paths.append(path)
        return load_configuration(paths)

    def test_parses_paths_shorthand_and_expanded_contracts(self) -> None:
        configuration = self.load(
            """\
schema_version: 3
checks:
  toolchain:
    paths: [go, gofmt]
  powershell:
    paths:
      pwsh:
        locations: [\"%ProgramFiles%/WindowsApps\"]
        candidate_count: 2
"""
        )

        toolchain, powershell = configuration.checks
        self.assertEqual(
            tuple(contract.command for contract in toolchain.paths),
            ("go", "gofmt"),
        )
        self.assertEqual(powershell.paths[0].command, "pwsh")
        self.assertEqual(
            powershell.paths[0].expected_locations,
            ("%ProgramFiles%/WindowsApps",),
        )
        self.assertEqual(powershell.paths[0].candidate_count, 2)

    def test_normalizes_shorthand_before_merging_overlays(self) -> None:
        configuration = self.load(
            """\
schema_version: 3
checks:
  fzf:
    paths: [fzf]
""",
            """\
schema_version: 3
checks:
  fzf:
    paths: [fzf-tmux]
""",
        )

        self.assertEqual(
            tuple(contract.command for contract in configuration.checks[0].paths),
            ("fzf", "fzf-tmux"),
        )

    def test_rejects_schema_version_two(self) -> None:
        with self.assertRaisesRegex(ConfigurationError, "schema_version must be 3"):
            self.load(
                """\
schema_version: 2
checks:
  git:
    path: [git]
"""
            )

    def test_rejects_singular_path_in_schema_version_three(self) -> None:
        with self.assertRaisesRegex(ConfigurationError, "unknown key 'path'"):
            self.load(
                """\
schema_version: 3
checks:
  git:
    path: [git]
"""
            )

    def test_rejects_duplicate_shorthand_commands(self) -> None:
        with self.assertRaisesRegex(ConfigurationError, "duplicate commands"):
            self.load(
                """\
schema_version: 3
checks:
  go:
    paths: [go, go]
"""
            )


if __name__ == "__main__":
    unittest.main()
