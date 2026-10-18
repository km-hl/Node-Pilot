#!/usr/bin/env python3
"""Compare release payload to Git HEAD, verify digest and bound installer; no secrets printed."""
import argparse
import hashlib
import re
import subprocess
import tarfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def git(*args):
    return subprocess.run(["git", *args], cwd=ROOT, capture_output=True, check=True).stdout


def audit_release(archive_path, installer_path, repo, tag):
    if (not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repo)
            or not re.fullmatch(r"v\d+\.\d+\.\d+", tag)):
        raise SystemExit("Release audit FAILED: invalid repository or tag")
    source_version = re.findall(rb"^__version__\s*=\s*['\"]([0-9]+\.[0-9]+\.[0-9]+)['\"]\s*$",
                                git("show", "HEAD:nodepilot_pkg/__init__.py"), re.MULTILINE)
    if source_version != [tag[1:].encode()] or archive_path.name != f"nodepilot-{tag[1:]}.tar.gz":
        raise SystemExit("Release audit FAILED: source version, tag or archive filename mismatch")
    names = [x for x in git("ls-tree", "-r", "--name-only", "HEAD").decode().splitlines()
             if x == "nodepilot" or re.fullmatch(r"nodepilot_pkg/[a-z_]+\.py", x)]
    expected = set(names)
    with tarfile.open(archive_path, "r:gz") as archive:
        seen = set()
        for entry in archive:
            if not entry.isfile() or entry.name not in expected or entry.name in seen or entry.size > 256 * 1024:
                raise SystemExit("Release archive FAILED: unknown, duplicate, non-regular or oversized entry")
            data = archive.extractfile(entry).read()
            if data != git("show", "HEAD:" + entry.name):
                raise SystemExit("Release archive FAILED: content differs from reviewed public Git HEAD")
            seen.add(entry.name)
        if seen != expected:
            raise SystemExit("Release archive FAILED: missing public manager files")
    digest = hashlib.sha256(archive_path.read_bytes()).hexdigest()
    checksum = archive_path.with_suffix(archive_path.suffix + ".sha256").read_text().split()
    installer_bytes = installer_path.read_bytes()
    installer = installer_bytes.decode("utf-8")
    url = f"https://github.com/{repo}/releases/download/{tag}/{archive_path.name}"
    if (checksum != [digest, archive_path.name]
            or re.findall(r"^PAYLOAD_SHA256='([^']*)'$", installer, re.MULTILINE) != [digest]
            or re.findall(r"^PAYLOAD_URL='([^']*)'$", installer, re.MULTILINE) != [url]):
        raise SystemExit("Release archive FAILED: digest or installer binding mismatch")
    if installer_bytes != git("show", "HEAD:install.sh"):
        raise SystemExit("Release installer FAILED: not the same reviewed installer as public source")
    print(f"Release audit OK: {len(seen)} exact public manager files, no tests/runtime data/binaries, SHA-256 {digest}")


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--archive", type=Path, default=ROOT / "dist/nodepilot-0.4.0.tar.gz")
    p.add_argument("--installer", type=Path, default=ROOT / "dist/install.sh")
    p.add_argument("--repo", default="km-hl/Node-Pilot")
    p.add_argument("--tag", default="v0.4.0")
    args = p.parse_args()
    audit_release(args.archive, args.installer, args.repo, args.tag)


if __name__ == "__main__":
    main()
