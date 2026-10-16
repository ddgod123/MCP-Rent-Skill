"""Release documentation must describe the package being shipped."""
from __future__ import annotations

import importlib.util
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch


SPEC = importlib.util.spec_from_file_location("validate_release", Path(__file__).with_name("validate_release.py"))
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class ReleaseDocumentationTests(unittest.TestCase):
    def test_stale_readme_or_skill_minimum_blocks_release(self) -> None:
        with tempfile.TemporaryDirectory() as directory, patch.object(MODULE, "ROOT", Path(directory)):
            root = Path(directory)
            (root / "CHANGELOG.md").write_text("# Changelog\n\n## 1.2.3\n\n- Correct release notes.\n")
            for readme_version, readme_min, skill_min, valid in (
                ("1.2.3", "4.5.6", "4.5.6", True),
                ("1.2.2", "4.5.6", "4.5.6", False),
                ("1.2.3", "4.5.5", "4.5.6", False),
                ("1.2.3", "4.5.6", "4.5.5", False),
            ):
                with self.subTest(readme_version=readme_version, readme_min=readme_min, skill_min=skill_min):
                    (root / "README.md").write_text(f"Skill: rentpro-rent {readme_version}\n最低 MCP: {readme_min}\n")
                    (root / "SKILL.md").write_text(f"---\nmetadata:\n  mcp_min_version: {skill_min}\n---\n")
                    manifest = {"latest_version": "1.2.3", "mcp_min_version": "4.5.6"}
                    if valid:
                        self.assertEqual(MODULE.validate_documentation(manifest), "- Correct release notes.")
                    else:
                        with self.assertRaises(AssertionError):
                            MODULE.validate_documentation(manifest)

    def test_notes_are_exactly_for_the_release_and_missing_or_empty_entries_fail(self) -> None:
        with tempfile.TemporaryDirectory() as directory, patch.object(MODULE, "ROOT", Path(directory)):
            changelog = Path(directory) / "CHANGELOG.md"
            changelog.write_text("# Changelog\n\n## 1.2.4\n\n- New.\n\n## 1.2.3\n\n- Target.\n\n## 1.2.2\n\n- Old.\n")
            self.assertEqual(MODULE.release_notes("1.2.3"), "- Target.")
            with self.assertRaises(AssertionError):
                MODULE.release_notes("1.2.5")
            changelog.write_text("## 1.2.3\n\n## 1.2.2\n\n- Old.\n")
            with self.assertRaises(AssertionError):
                MODULE.release_notes("1.2.3")


if __name__ == "__main__":
    unittest.main()
