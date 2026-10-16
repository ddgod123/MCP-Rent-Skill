#!/usr/bin/env python3
"""Unit tests for the tokenless and administrator installation modes."""
from __future__ import annotations

import argparse
import importlib.util
import io
from pathlib import Path
import os
import sys
import tempfile
import unittest
from unittest.mock import patch


SCRIPT = Path(__file__).with_name("install_rentpro.py")
if not SCRIPT.exists():
    SCRIPT = Path(__file__).resolve().parents[1] / "install_rentpro.py"
SPEC = importlib.util.spec_from_file_location("install_rentpro", SCRIPT)
if SPEC is None or SPEC.loader is None:
    raise RuntimeError(f"cannot load {SCRIPT}")
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


def token_args(**overrides: object) -> argparse.Namespace:
    values = {
        "token": None,
        "token_file": None,
        "token_env": None,
        "no_prompt": True,
    }
    values.update(overrides)
    return argparse.Namespace(**values)


class InstallRentProTests(unittest.TestCase):
    def test_installer_discovers_latest_without_forcing_downgrade(self) -> None:
        with patch.object(MODULE.subprocess, "run") as run:
            MODULE.skill_update(MODULE.DEFAULT_MANIFEST, Path("/selected-host/rentpro-rent/SKILL.md"))
        command = run.call_args.args[0]
        self.assertEqual(command[command.index("--manifest-url") + 1], "https://api.smartrent.xin/api/public/rentpro-install")
        self.assertNotIn("--force", command)
        self.assertEqual(command[command.index("--local-skill") + 1], "/selected-host/rentpro-rent/SKILL.md")

    def test_dsh_installer_passes_target_to_updater_without_touching_workbuddy(self) -> None:
        with tempfile.TemporaryDirectory() as directory, patch.object(Path, "home", return_value=Path(directory)), patch(
            "sys.argv", ["installer", "--host", "deepseekharness", "--skip-mcp"]
        ), patch.object(MODULE, "skill_update") as update, patch.object(MODULE, "install_workbuddy") as configure, patch(
            "sys.stdout", new_callable=io.StringIO
        ):
            self.assertEqual(MODULE.main(), 0)
            update.assert_called_once_with(MODULE.DEFAULT_MANIFEST, Path(directory) / ".dsh/skills/rentpro-rent/SKILL.md")
            configure.assert_not_called()
            self.assertFalse((Path(directory) / ".workbuddy").exists())

    def test_unknown_host_uses_explicit_native_directory(self) -> None:
        with tempfile.TemporaryDirectory() as directory, patch("sys.argv", [
            "installer", "--host", "generic", "--skill-dir", directory,
        ]), patch.object(MODULE, "skill_update") as update, patch("sys.stdout", new_callable=io.StringIO):
            self.assertEqual(MODULE.main(), 0)
            update.assert_called_once_with(MODULE.DEFAULT_MANIFEST, Path(directory) / "SKILL.md")

    def test_no_host_does_not_default_to_workbuddy(self) -> None:
        with patch.dict(os.environ, {}, clear=True), patch("sys.argv", ["installer"]), patch.object(
            MODULE, "skill_update"
        ) as update, self.assertRaisesRegex(RuntimeError, "无法确定当前宿主"):
            MODULE.main()
        update.assert_not_called()

    def test_other_hosts_never_write_workbuddy_mcp_configuration(self) -> None:
        with tempfile.TemporaryDirectory() as directory, patch.object(Path, "home", return_value=Path(directory)), patch(
            "sys.argv", ["installer", "--host", "dsh", "--configure-mcp", "--skip-healthcheck", "--token-env", "TEST_RENTPRO_TOKEN"]
        ), patch.dict(os.environ, {"TEST_RENTPRO_TOKEN": "x" * 32}), patch.object(
            MODULE, "install_workbuddy"
        ) as configure, patch.object(MODULE, "skill_update"), patch("sys.stdout", new_callable=io.StringIO):
            self.assertEqual(MODULE.main(), 0)
            configure.assert_not_called()

    def test_token_file_is_preferred_for_admin_mode(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "mcp-token"
            path.write_text("admin-secret\n", encoding="utf-8")
            self.assertEqual(
                MODULE.resolve_token(token_args(token_file=str(path))),
                "admin-secret",
            )

    def test_token_env_is_supported_without_printing(self) -> None:
        previous = os.environ.get("TEST_RENTPRO_TOKEN")
        os.environ["TEST_RENTPRO_TOKEN"] = "env-secret"
        try:
            self.assertEqual(
                MODULE.resolve_token(token_args(token_env="TEST_RENTPRO_TOKEN")),
                "env-secret",
            )
        finally:
            if previous is None:
                os.environ.pop("TEST_RENTPRO_TOKEN", None)
            else:
                os.environ["TEST_RENTPRO_TOKEN"] = previous

    def test_non_interactive_mode_requires_a_source(self) -> None:
        with self.assertRaisesRegex(RuntimeError, "--token-file 或 --token-env"):
            MODULE.resolve_token(token_args())

    def test_conflicting_token_sources_are_rejected(self) -> None:
        with self.assertRaisesRegex(RuntimeError, "只能选择一个"):
            MODULE.resolve_token(
                token_args(token="inline", token_env="TEST_RENTPRO_TOKEN")
            )


if __name__ == "__main__":
    unittest.main()
