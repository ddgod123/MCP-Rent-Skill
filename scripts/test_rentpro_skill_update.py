#!/usr/bin/env python3
"""Unit tests for the standalone RentPro Skill updater."""
from __future__ import annotations

import importlib.util
import argparse
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch


SCRIPT = Path(__file__).with_name("rentpro_skill_update.py")
if not SCRIPT.exists():
    SCRIPT = Path(__file__).resolve().parents[1] / "rentpro_skill_update.py"
SPEC = importlib.util.spec_from_file_location("rentpro_skill_update", SCRIPT)
if SPEC is None or SPEC.loader is None:
    raise RuntimeError(f"cannot load {SCRIPT}")
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


def release_payload(version: str = "0.18.7") -> tuple[dict, dict[str, bytes]]:
    contents = {
        "SKILL.md": f"---\nname: rentpro-rent\nmetadata:\n  version: {version}\n---\n".encode(),
        "rentpro_skill_update.py": b"# verified updater\n",
        "install_rentpro.py": b"# verified installer\n",
    }
    manifest = {
        "schema_version": 2, "skill_name": "rentpro-rent",
        "latest_version": version, "source_ref": f"rentpro-rent-v{version}",
        "files": [{
            "path": name, "role": "skill" if name == "SKILL.md" else "resource",
            "sha256": MODULE.sha256_bytes(content),
            "download_url": MODULE.cloud_file_url(name, version),
            "fallback_download_url": MODULE.github_file_url(name, f"rentpro-rent-v{version}"),
        } for name, content in contents.items()],
    }
    return manifest, contents


class RentProSkillUpdateTests(unittest.TestCase):
    def test_default_discovery_finds_future_release_without_changing_script(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            local = Path(directory) / "SKILL.md"
            local.write_bytes(release_payload("0.18.7")[1]["SKILL.md"])
            for version in ("0.18.8", "0.19.0"):
                with self.subTest(version=version):
                    manifest, _ = release_payload(version)
                    pinned = MODULE.cloud_file_url("rentpro_skill_release.json", version)
                    responses = {
                        MODULE.DEFAULT_MANIFEST_URL: json.dumps({
                            "skill_name": "rentpro-rent", "version": version,
                            "manifest_url": pinned,
                        }).encode(),
                        pinned: json.dumps(manifest).encode(),
                    }
                    with patch.object(MODULE, "_github_token", return_value=None), patch.object(
                        MODULE, "fetch_url", side_effect=lambda url, *_: responses[url]
                    ):
                        status = MODULE.build_status(local, MODULE.build_parser().get_default("manifest_url"), 1)
                    self.assertEqual(status["latest_version"], version)
                    self.assertTrue(status["update_available"])
                    self.assertEqual(status["resolved_manifest_url"], pinned)

    def test_default_update_installs_verified_package_and_recheck_is_current(self) -> None:
        version = "0.18.8"
        manifest, contents = release_payload(version)
        pinned = MODULE.cloud_file_url("rentpro_skill_release.json", version)
        responses = {
            MODULE.DEFAULT_MANIFEST_URL: json.dumps({
                "skill_name": "rentpro-rent", "version": version, "manifest_url": pinned,
            }).encode(),
            pinned: json.dumps(manifest).encode(),
            **{MODULE.cloud_file_url(name, version): content for name, content in contents.items()},
        }
        with tempfile.TemporaryDirectory() as directory:
            local = Path(directory) / "SKILL.md"
            local.write_bytes(release_payload("0.18.7")[1]["SKILL.md"])
            args = argparse.Namespace(local_skill=local, manifest_url=MODULE.DEFAULT_MANIFEST_URL,
                                      timeout=1, force=False, yes=True, json=False)
            with patch.object(MODULE, "_github_token", return_value=None), patch.object(
                MODULE, "fetch_url", side_effect=lambda url, *_: responses[url]
            ), patch("sys.stdout", new_callable=io.StringIO):
                self.assertEqual(MODULE.command_update(args), 0)
                self.assertFalse(MODULE.build_status(local, MODULE.DEFAULT_MANIFEST_URL, 1)["update_available"])
            for name, content in contents.items():
                self.assertEqual(local.parent.joinpath(name).read_bytes(), content)

    def test_pinned_cloud_failure_uses_same_version_github_mirror(self) -> None:
        manifest, _ = release_payload("0.18.8")
        pinned = MODULE.cloud_file_url("rentpro_skill_release.json", "0.18.8")
        fallback = MODULE.github_file_url("rentpro_skill_release.json", "rentpro-rent-v0.18.8")
        def fetch(url, *_):
            if url == pinned:
                raise MODULE.UpdateError("cloud unavailable")
            self.assertEqual(url, fallback)
            return json.dumps(manifest).encode()
        with patch.object(MODULE, "_github_token", return_value=None), patch.object(MODULE, "fetch_url", side_effect=fetch):
            self.assertEqual(MODULE.load_manifest(pinned, 1)["latest_version"], "0.18.8")

    def test_pinned_fallback_rejects_another_version(self) -> None:
        manifest, _ = release_payload("0.18.5")
        with patch.object(MODULE, "_github_token", return_value=None), patch.object(
            MODULE, "fetch_url", side_effect=[MODULE.UpdateError("unavailable"), json.dumps(manifest).encode()]
        ):
            with self.assertRaisesRegex(MODULE.UpdateError, "版本不一致"):
                MODULE.load_manifest(MODULE.cloud_file_url("rentpro_skill_release.json", "0.18.8"), 1)

    def test_discovery_network_failure_uses_current_github_manifest(self) -> None:
        manifest, _ = release_payload("0.18.8")
        with patch.object(MODULE, "_github_token", return_value=None), patch.object(
            MODULE, "fetch_url", side_effect=[MODULE.UpdateError("unavailable"), json.dumps(manifest).encode()]
        ) as fetch:
            result = MODULE.load_manifest(MODULE.DEFAULT_MANIFEST_URL, 1)
        self.assertEqual(result["latest_version"], "0.18.8")
        self.assertEqual(fetch.call_args_list[-1].args[0], MODULE.DEFAULT_GITHUB_MANIFEST_URL)

    def test_invalid_discovery_does_not_switch_sources(self) -> None:
        with patch.object(MODULE, "_github_token", return_value=None), patch.object(
            MODULE, "fetch_url", return_value=b'{"skill_name":"another-skill"}'
        ) as fetch:
            with self.assertRaises(MODULE.UpdateError):
                MODULE.load_manifest(MODULE.DEFAULT_MANIFEST_URL, 1)
        self.assertEqual(fetch.call_count, 1)

    def test_force_update_cannot_downgrade(self) -> None:
        args = argparse.Namespace(local_skill=Path("unused"), manifest_url=MODULE.DEFAULT_MANIFEST_URL,
                                  timeout=1, force=True, yes=True)
        with patch.object(MODULE, "build_status", return_value={
            "local_version": "0.18.8", "latest_version": "0.18.7", "update_available": False,
        }), patch.object(MODULE, "update_installed_skill") as update:
            with self.assertRaisesRegex(MODULE.UpdateError, "禁止降级"):
                MODULE.command_update(args)
        update.assert_not_called()

    def test_github_credentials_are_not_sent_to_install_or_cloud(self) -> None:
        for url in (MODULE.DEFAULT_MANIFEST_URL, MODULE.cloud_file_url("SKILL.md", "0.18.8")):
            with self.subTest(url=url), patch.object(MODULE, "urlopen", return_value=io.BytesIO(b"{}")) as open_url:
                MODULE.fetch_url(url, "private-github-token", 1)
                request = open_url.call_args.args[0]
                self.assertIsNone(request.get_header("Authorization"))
                if url == MODULE.DEFAULT_MANIFEST_URL:
                    self.assertEqual(request.get_header("Accept"), "application/json")

    def test_parse_version_accepts_optional_v_prefix(self) -> None:
        self.assertEqual(MODULE.parse_version("0.6.0"), (0, 6, 0))
        self.assertEqual(MODULE.parse_version("v1.2.3"), (1, 2, 3))

    def test_read_skill_metadata(self) -> None:
        content = b"""---
name: rentpro-rent
version: 0.6.0
---

# Skill
"""
        metadata = MODULE.read_skill_metadata(content)
        self.assertEqual(metadata.name, "rentpro-rent")
        self.assertEqual(metadata.version, "0.6.0")

    def test_read_skill_metadata_from_standard_metadata_block(self) -> None:
        content = b"""---
name: rentpro-rent
description: test
metadata:
  version: 0.6.0
  mcp_min_version: 0.6.0
---
"""
        metadata = MODULE.read_skill_metadata(content)
        self.assertEqual(metadata.name, "rentpro-rent")
        self.assertEqual(metadata.version, "0.6.0")

    def test_decode_github_content_handles_raw_and_api_json(self) -> None:
        raw = b'{"latest_version":"0.6.0"}'
        self.assertEqual(MODULE.decode_github_content(raw), raw)

        encoded = "eyJsYXRlc3RfdmVyc2lvbiI6IjAuNi4wIn0="
        api_payload = ('{"content":"' + encoded + '"}').encode()
        self.assertEqual(
            MODULE.decode_github_content(api_payload),
            b'{"latest_version":"0.6.0"}',
        )

    def test_local_metadata_and_digest(self) -> None:
        content = b"""---
name: rentpro-rent
version: 0.6.0
---
"""
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "SKILL.md"
            path.write_bytes(content)
            metadata, digest = MODULE.local_metadata(path)
            self.assertEqual(metadata.version, "0.6.0")
            self.assertEqual(digest, MODULE.sha256_bytes(content))

    def test_project_skill_candidates_support_private_and_public_layouts(self) -> None:
        candidates = {str(path) for path in MODULE.PROJECT_SKILL_CANDIDATES}
        self.assertIn(str(MODULE.ROOT / "skills/rentpro-rent/SKILL.md"), candidates)
        self.assertIn(str(MODULE.ROOT / "SKILL.md"), candidates)

    def test_github_url_is_fixed_to_repository(self) -> None:
        MODULE.validate_github_url(
            "https://api.github.com/repos/ddgod123/MCP-Rent-Skill/contents/file"
        )
        with self.assertRaises(MODULE.UpdateError):
            MODULE.validate_github_url(
                "https://api.github.com/repos/another/project/contents/file"
            )

    def test_update_replaces_skill_and_keeps_backup(self) -> None:
        content = b"""---
name: rentpro-rent
version: 0.6.0
---
"""
        manifest = {
            "skill_name": "rentpro-rent",
            "latest_version": "0.6.0",
            "update_channel": "stable",
            "source_ref": "rentpro-rent-v0.6.0",
            "skill_path": "SKILL.md",
            "sha256": MODULE.sha256_bytes(content),
            "download_url": "https://api.github.com/repos/ddgod123/MCP-Rent-Skill/contents/SKILL.md",
        }
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "SKILL.md"
            path.write_text("old skill", encoding="utf-8")
            with patch.object(MODULE, "fetch_url", return_value=content):
                previous = MODULE.update_installed_skill(path, manifest, 1)
            self.assertEqual(path.read_bytes(), content)
            self.assertIsNotNone(previous)
            self.assertEqual(previous.read_text(encoding="utf-8"), "old skill")

    def test_schema_v2_updates_all_files_after_validating_each_digest(self) -> None:
        skill = b"""---
name: rentpro-rent
version: 0.10.1
---
"""
        reference = b"# reference\n"
        updater = b"#!/usr/bin/env python3\n"
        files = [
            MODULE.ManifestFile(
                "SKILL.md",
                "skill",
                "https://api.github.com/repos/ddgod123/MCP-Rent-Skill/contents/SKILL.md",
                MODULE.sha256_bytes(skill),
            ),
            MODULE.ManifestFile(
                "references/tool-routing.md",
                "reference",
                "https://api.github.com/repos/ddgod123/MCP-Rent-Skill/contents/references/tool-routing.md",
                MODULE.sha256_bytes(reference),
            ),
            MODULE.ManifestFile(
                "rentpro_skill_update.py",
                "updater",
                "https://api.github.com/repos/ddgod123/MCP-Rent-Skill/contents/rentpro_skill_update.py",
                MODULE.sha256_bytes(updater),
            ),
        ]
        manifest = {
            "schema_version": 2,
            "skill_name": "rentpro-rent",
            "latest_version": "0.10.1",
            "_files": files,
        }
        responses = {
            files[0].download_url: skill,
            files[1].download_url: reference,
            files[2].download_url: updater,
        }
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            local_skill = root / "SKILL.md"
            local_skill.write_text("old", encoding="utf-8")
            with patch.object(MODULE, "fetch_url", side_effect=lambda url, token, timeout: responses[url]):
                previous = MODULE.update_installed_skill(local_skill, manifest, 1)
            self.assertEqual(local_skill.read_bytes(), skill)
            self.assertEqual(
                (root / "references/tool-routing.md").read_bytes(),
                reference,
            )
            self.assertEqual(
                (root / "rentpro_skill_update.py").read_bytes(),
                updater,
            )
            self.assertTrue(previous)

    def test_install_from_project_copies_references_to_workbuddy_layout(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            local_skill = root / "workbuddy/skills/rentpro-rent/SKILL.md"
            update_script = root / "workbuddy/skills/rentpro-rent/rentpro_skill_update.py"
            MODULE.install_from_project(local_skill, update_script)
            self.assertTrue(local_skill.exists())
            self.assertTrue(local_skill.parent.joinpath("references/tool-routing.md").exists())
            self.assertTrue(local_skill.parent.joinpath("references/response-format.md").exists())
            self.assertTrue(update_script.exists())
            self.assertEqual(
                MODULE.read_skill_metadata(local_skill.read_bytes()).name,
                "rentpro-rent",
            )

    def test_manifest_rejects_unsafe_relative_paths(self) -> None:
        with self.assertRaises(MODULE.UpdateError):
            MODULE.manifest_files(
                {
                    "schema_version": 2,
                    "source_ref": "main",
                    "files": [
                        {
                            "path": "../outside",
                            "sha256": "a" * 64,
                            "download_url": "https://api.github.com/repos/ddgod123/MCP-Rent-Skill/contents/outside",
                        }
                    ],
                }
            )


class HostIsolationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.home = Path(self.directory.name)
        self.home_patch = patch.object(MODULE.Path, "home", return_value=self.home)
        self.home_patch.start()
        self.addCleanup(self.home_patch.stop)
        self.env_patch = patch.dict(os.environ, {}, clear=True)
        self.env_patch.start()
        self.addCleanup(self.env_patch.stop)
        self.repo_script = self.home / "repo/scripts/rentpro_skill_update.py"

    def installed(self, host: str) -> Path:
        path = self.home / f".{host}/skills/rentpro-rent/SKILL.md"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(release_payload("0.18.7")[1]["SKILL.md"])
        return path

    def test_running_dsh_update_does_not_change_workbuddy(self) -> None:
        for host, other in (("dsh", "workbuddy"), ("workbuddy", "dsh")):
            with self.subTest(host=host):
                local, untouched = self.installed(host), self.installed(other)
                original = untouched.read_bytes()
                other_files = {path.name: path.read_bytes() for path in untouched.parent.iterdir() if path.is_file()}
                manifest, contents = release_payload("0.18.10")
                pinned = MODULE.cloud_file_url("rentpro_skill_release.json", "0.18.10")
                responses = {
                    MODULE.DEFAULT_MANIFEST_URL: json.dumps({
                        "skill_name": "rentpro-rent", "version": "0.18.10", "manifest_url": pinned,
                    }).encode(),
                    pinned: json.dumps(manifest).encode(),
                    **{MODULE.cloud_file_url(name, "0.18.10"): value for name, value in contents.items()},
                }
                with patch.object(MODULE, "__file__", str(local.with_name("rentpro_skill_update.py"))), patch.object(
                    MODULE, "_github_token", return_value=None
                ), patch.object(MODULE, "fetch_url", side_effect=lambda url, *_: responses[url]), patch(
                    "sys.argv", ["updater", "update", "--yes"]
                ), patch("sys.stdout", new_callable=io.StringIO):
                    self.assertEqual(MODULE.main(), 0)
                for name, content in contents.items():
                    self.assertEqual(local.parent.joinpath(name).read_bytes(), content)
                self.assertEqual(untouched.read_bytes(), original)
                self.assertEqual({path.name: path.read_bytes() for path in untouched.parent.iterdir() if path.is_file()}, other_files)

    def test_explicit_hosts_and_aliases_support_first_install(self) -> None:
        for host in MODULE.HOST_SKILL_ROOTS:
            with self.subTest(host=host):
                target = MODULE.resolve_install_target(host, script_path=self.repo_script)
                self.assertEqual(target.host, host)
                self.assertEqual(target.local_skill, MODULE.host_skill_roots(host)[0] / "rentpro-rent/SKILL.md")
        self.assertEqual(MODULE.resolve_install_target("DeepSeekHarness").host, "dsh")

    def test_explicit_host_overrides_copied_script_location(self) -> None:
        workbuddy = self.installed("workbuddy")
        target = MODULE.resolve_install_target("dsh", script_path=workbuddy.with_name("install_rentpro.py"))
        self.assertEqual(target.host, "dsh")
        self.assertEqual(workbuddy.read_bytes(), release_payload("0.18.7")[1]["SKILL.md"])

    def test_unknown_context_does_not_choose_another_installed_app(self) -> None:
        self.installed("workbuddy")
        with self.assertRaisesRegex(MODULE.UpdateError, "无法确定当前宿主"):
            MODULE.resolve_install_target(script_path=self.repo_script)
        self.installed("dsh")
        with self.assertRaises(MODULE.UpdateError):
            MODULE.resolve_install_target(script_path=self.repo_script)

    def test_public_repo_is_not_an_installed_host(self) -> None:
        repo = self.home / "MCP-Rent-Skill"
        repo.mkdir()
        (repo / ".git").mkdir()
        (repo / "SKILL.md").write_bytes(release_payload()[1]["SKILL.md"])
        with self.assertRaisesRegex(MODULE.UpdateError, "无法确定当前宿主"):
            MODULE.resolve_install_target(script_path=repo / "rentpro_skill_update.py")

    def test_custom_host_directory_and_environment(self) -> None:
        custom = self.home / "native-host/custom-skills/rentpro-rent"
        target = MODULE.resolve_install_target("generic", skill_dir=custom)
        self.assertEqual(target.local_skill, custom / "SKILL.md")
        with patch.dict(os.environ, {"RENTPRO_SKILL_DIR": str(custom)}):
            self.assertEqual(MODULE.resolve_install_target(script_path=self.repo_script).local_skill, custom / "SKILL.md")
        with patch.dict(os.environ, {"RENTPRO_HOST": "dsh"}):
            self.assertEqual(MODULE.resolve_install_target(script_path=self.repo_script).host, "dsh")

    def test_installed_location_wins_over_inherited_environment(self) -> None:
        dsh = self.installed("dsh")
        with patch.dict(os.environ, {"RENTPRO_HOST": "workbuddy"}):
            self.assertEqual(MODULE.resolve_install_target(script_path=dsh.with_name("install_rentpro.py")).local_skill, dsh)

    def test_codex_uses_existing_copy_and_rejects_ambiguous_copies(self) -> None:
        local = self.home / ".agents/skills/rentpro-rent/SKILL.md"
        local.parent.mkdir(parents=True)
        local.write_bytes(release_payload()[1]["SKILL.md"])
        self.assertEqual(MODULE.resolve_install_target("codex").local_skill, local)
        self.installed("codex")
        with self.assertRaisesRegex(MODULE.UpdateError, "多个安装副本"):
            MODULE.resolve_install_target("codex")

    def test_codex_custom_home(self) -> None:
        with patch.dict(os.environ, {"CODEX_HOME": str(self.home / "codex-custom")}):
            self.assertEqual(MODULE.resolve_install_target("codex").local_skill,
                             self.home / "codex-custom/skills/rentpro-rent/SKILL.md")

    def test_rejects_conflicting_host_and_path_or_other_skill(self) -> None:
        workbuddy = self.installed("workbuddy")
        with self.assertRaisesRegex(MODULE.UpdateError, "不一致"):
            MODULE.resolve_install_target("dsh", local_skill=workbuddy)
        workbuddy.write_text("---\nname: another-skill\n---\n")
        with self.assertRaisesRegex(MODULE.UpdateError, "拒绝覆盖"):
            MODULE.resolve_install_target(local_skill=workbuddy)
        with self.assertRaisesRegex(MODULE.UpdateError, "只能选择一个"):
            MODULE.resolve_install_target(local_skill=workbuddy, skill_dir=workbuddy.parent)

    def test_install_updater_follows_selected_skill_and_rejects_cross_host_path(self) -> None:
        local = self.home / ".dsh/skills/rentpro-rent/SKILL.md"
        with patch("sys.argv", ["updater", "--host", "dsh", "install"]), patch.object(
            MODULE, "install_from_project"
        ) as install:
            MODULE.main()
        install.assert_called_once_with(local, local.with_name("rentpro_skill_update.py"))
        with self.assertRaisesRegex(MODULE.UpdateError, "同一目录"):
            MODULE.install_from_project(local, self.home / ".workbuddy/skills/rentpro-rent/rentpro_skill_update.py")
        self.assertFalse(local.exists())

    def test_invalid_env_auto_does_not_recurse(self) -> None:
        with patch.dict(os.environ, {"RENTPRO_HOST": "auto"}), self.assertRaises(MODULE.UpdateError):
            MODULE.resolve_install_target(script_path=self.repo_script)


class BootstrapTests(unittest.TestCase):
    def run_bootstrap(self, directory: str, *, corrupt: bool = False, offline: bool = False):
        source = SCRIPT.with_name("install_rentpro.sh").read_text().split("<<'PY'\n", 1)[1].rsplit("\nPY", 1)[0]
        manifest, contents = release_payload("0.18.8")
        pinned = MODULE.cloud_file_url("rentpro_skill_release.json", "0.18.8")
        responses = {
            MODULE.DEFAULT_MANIFEST_URL: json.dumps({
                "skill_name": "rentpro-rent", "version": "0.18.8", "manifest_url": pinned,
            }).encode(),
            pinned: json.dumps(manifest).encode(),
            MODULE.DEFAULT_GITHUB_MANIFEST_URL: json.dumps(manifest).encode(),
            **{MODULE.cloud_file_url(name, "0.18.8"): content for name, content in contents.items()},
        }
        if corrupt:
            responses[MODULE.cloud_file_url("rentpro_skill_update.py", "0.18.8")] = b"tampered"
        def open_url(request, **_):
            if offline and request.full_url == MODULE.DEFAULT_MANIFEST_URL:
                raise OSError("discovery unavailable")
            return io.BytesIO(responses[request.full_url])
        with patch("sys.argv", ["-", directory, "--skip-mcp"]), patch("urllib.request.urlopen", side_effect=open_url), patch("subprocess.run") as run:
            exec(compile(source, "install_rentpro.sh", "exec"), {})
            return run.call_args.args[0]

    def test_bootstrap_discovers_future_version_and_verifies_executables(self) -> None:
        for offline in (False, True):
            with self.subTest(offline=offline), tempfile.TemporaryDirectory() as directory:
                command = self.run_bootstrap(directory, offline=offline)
                self.assertEqual(Path(directory, "install_rentpro.py").read_bytes(), b"# verified installer\n")
                self.assertIn("--skip-mcp", command)
                expected = MODULE.github_file_url("rentpro_skill_release.json", "rentpro-rent-v0.18.8") if offline else MODULE.cloud_file_url("rentpro_skill_release.json", "0.18.8")
                self.assertEqual(command[command.index("--manifest-url") + 1], expected)

    def test_bootstrap_rejects_tampered_script_before_execution(self) -> None:
        with tempfile.TemporaryDirectory() as directory, patch("subprocess.run") as run:
            with self.assertRaisesRegex(SystemExit, "SHA-256"):
                self.run_bootstrap(directory, corrupt=True)
            self.assertEqual(list(Path(directory).iterdir()), [])
            run.assert_not_called()


if __name__ == "__main__":
    unittest.main()
