#!/usr/bin/env python3
"""Validate a RentPro Skill release before publishing it."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re
import subprocess
import sys
from urllib.parse import parse_qs, urlparse


ROOT = Path(__file__).resolve().parents[1]
TAG_RE = re.compile(r"^rentpro-rent-v(?P<version>\d+\.\d+\.\d+)$")


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def skill_version() -> str:
    content = (ROOT / "SKILL.md").read_text(encoding="utf-8")
    match = re.search(r"(?m)^\s*version:\s*([^\s]+)\s*$", content)
    if not match:
        raise AssertionError("SKILL.md is missing metadata.version")
    return match.group(1)


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate RentPro Skill release metadata")
    parser.add_argument("--tag", help="Expected immutable release tag")
    args = parser.parse_args()

    manifest = json.loads((ROOT / "rentpro_skill_release.json").read_text(encoding="utf-8"))
    assert manifest.get("schema_version") == 2, "schema_version must be 2"
    assert manifest.get("skill_name") == "rentpro-rent", "unexpected skill_name"
    assert manifest.get("update_channel") == "stable", "release must be stable"

    source_ref = manifest.get("source_ref")
    source_match = TAG_RE.fullmatch(source_ref or "")
    assert source_match, "stable source_ref must be an immutable rentpro-rent-vX.Y.Z tag"
    version = manifest.get("latest_version")
    assert source_match.group("version") == version, "tag and manifest version differ"
    assert skill_version() == version, "SKILL.md and manifest version differ"
    if args.tag:
        assert args.tag == source_ref, f"tag {args.tag!r} does not match manifest source_ref {source_ref!r}"

    for item in manifest.get("files", []):
        path = item.get("path")
        assert isinstance(path, str) and path and not path.startswith("/"), f"invalid manifest path: {path!r}"
        local = ROOT / path
        assert local.is_file(), f"manifest file missing: {path}"
        assert sha256(local) == item.get("sha256"), f"SHA-256 mismatch: {path}"
        parsed = urlparse(item.get("download_url", ""))
        assert parsed.scheme == "https", f"unsafe download URL: {path}"
        if parsed.netloc in {"smartrent.xin", "www.smartrent.xin"}:
            assert parsed.path.startswith(f"/skill/rentpro-rent/v{version}/"), f"cloud URL version mismatch: {path}"
        else:
            assert parsed.netloc == "api.github.com", f"unsafe download URL: {path}"
            assert parse_qs(parsed.query).get("ref") == [source_ref], f"download URL ref mismatch: {path}"
        fallback = item.get("fallback_download_url")
        assert isinstance(fallback, str) and urlparse(fallback).netloc == "api.github.com", f"missing GitHub fallback URL: {path}"
        assert parse_qs(urlparse(fallback).query).get("ref") == [source_ref], f"fallback ref mismatch: {path}"

    subprocess.run([sys.executable, "-m", "py_compile", "install_rentpro.py", "rentpro_skill_update.py", "scripts/validate_release.py"], cwd=ROOT, check=True)
    subprocess.run([sys.executable, "install_rentpro.py", "--help"], cwd=ROOT, check=True, stdout=subprocess.DEVNULL)
    print(f"release OK: {manifest['skill_name']} {version} ({source_ref})")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (AssertionError, OSError, subprocess.CalledProcessError, json.JSONDecodeError) as exc:
        print(f"release validation failed: {exc}", file=sys.stderr)
        raise SystemExit(1)
