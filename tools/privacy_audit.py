#!/usr/bin/env python3
"""Fail closed on upload scope or secret indicators. Never print matched values."""
import argparse
import getpass
import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TOP = {".gitignore", ".gitattributes", "README.md", "CONTRIBUTING.md", "LICENSE", "install.sh", "nodepilot"}
PATTERNS = [
    re.compile(rb"-----BEGIN (?:RSA |EC |OPENSSH |ENCRYPTED )?PRIVATE KEY-----"),
    re.compile(rb"\b(?:gh[pousr]_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{40,})\b"),
    re.compile(rb"\bAKIA[A-Z0-9]{16}\b"),
    re.compile(rb"(?i)(?:password|private_key|privateKey|api_token|secret)\s*[:=]\s*['\"]([A-Za-z0-9_+/-]{8,})['\"]"),
]


def allowed(name):
    if name in TOP:
        return True
    return bool(re.fullmatch(r"(?:nodepilot_pkg|tests|tools)/[a-z_]+\.py|docs/[A-Z_]+\.md|\.github/workflows/[a-z_]+\.yml", name))


def audit(paths, private_values=(), reader=None):
    issues = []
    for name in paths:
        if not allowed(name):
            issues.append((name, "file is outside public source allowlist"))
            continue
        if reader:
            try:
                content = reader(name)
            except (ValueError, subprocess.CalledProcessError):
                issues.append((name, "unreadable, non-regular or oversized Git blob"))
                continue
        else:
            path = ROOT / name
            if path.is_symlink() or not path.is_file() or path.stat().st_size > 256 * 1024:
                issues.append((name, "symlink, missing or oversized file"))
                continue
            content = path.read_bytes()
        if any(value and value.encode() in content for value in private_values):
            issues.append((name, "operator-supplied private value present"))
        for pattern in PATTERNS:
            if pattern.search(content):
                issues.append((name, "secret indicator requires review"))
    return issues


def git_candidates(staged=False):
    command = ["git", "ls-files", "--stage", "-z"] if staged else ["git", "ls-tree", "-r", "-z", "HEAD"]
    raw = subprocess.run(command, cwd=ROOT, capture_output=True, check=True).stdout
    entries = {}
    for line in raw.split(b"\0"):
        if not line:
            continue
        info, name = line.split(b"\t", 1)
        parts = info.decode().split()
        mode, oid = parts[0], parts[1] if staged else parts[2]
        valid = mode in {"100644", "100755"} and (parts[2] == "0" if staged else parts[1] == "blob")
        entries[name.decode()] = (valid, oid)
    def reader(name):
        valid, oid = entries[name]
        if not valid:
            raise ValueError("only regular files")
        size = int(subprocess.run(["git", "cat-file", "-s", oid], cwd=ROOT, capture_output=True, check=True).stdout)
        if size > 256 * 1024:
            raise ValueError("oversized")
        return subprocess.run(["git", "cat-file", "-p", oid], cwd=ROOT, capture_output=True, check=True).stdout
    return sorted(entries), reader


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--staged", action="store_true")
    p.add_argument("--private-input", action="store_true", help="隐式输入会话密码/地址，扫描后即丢弃")
    args = p.parse_args()
    paths, reader = git_candidates(args.staged)
    if not paths:
        raise SystemExit("No upload candidates: refusing empty audit")
    private_values = []
    if args.private_input:
        private_values = [getpass.getpass("Session secret (hidden): "), getpass.getpass("Private host/address (hidden, optional): ")]
    issues = audit(paths, private_values, reader)
    del private_values
    for name, reason in issues:
        print(name + ": " + reason)
    if issues:
        raise SystemExit("Upload audit FAILED; matched values were not printed")
    print(f"Upload audit OK: {len(paths)} allowlisted public source files, no detected private values/secret indicators")
    for name in paths:
        print(name)


if __name__ == "__main__":
    main()
