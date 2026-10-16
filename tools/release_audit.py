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


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--archive", type=Path, default=ROOT / "dist/nodepilot-0.4.0.tar.gz")
    p.add_argument("--installer", type=Path, default=ROOT / "dist/install.sh")
    p.add_argument("--repo", default="km-hl/Node-Pilot")
    p.add_argument("--tag", default="v0.4.0")
    args = p.parse_args()
    names = [x for x in git("ls-tree", "-r", "--name-only", "HEAD").decode().splitlines()
             if x == "nodepilot" or re.fullmatch(r"nodepilot_pkg/[a-z_]+\.py", x)]
    expected = set(names)
    with tarfile.open(args.archive, "r:gz") as archive:
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
    digest = hashlib.sha256(args.archive.read_bytes()).hexdigest()
    checksum = args.archive.with_suffix(args.archive.suffix + ".sha256").read_text().split()[0]
    installer = args.installer.read_text(encoding="utf-8")
    url = f"https://github.com/{args.repo}/releases/download/{args.tag}/{args.archive.name}"
    if checksum != digest or f"PAYLOAD_SHA256='{digest}'" not in installer or f"PAYLOAD_URL='{url}'" not in installer:
        raise SystemExit("Release archive FAILED: digest or installer binding mismatch")
    if installer != (ROOT / "install.sh").read_text(encoding="utf-8"):
        raise SystemExit("Release installer FAILED: not the same reviewed installer as public source")
    print(f"Release audit OK: {len(seen)} exact public manager files, no tests/runtime data/binaries, SHA-256 {digest}")


if __name__ == "__main__":
    main()
