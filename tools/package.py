#!/usr/bin/env python3
"""Reproducible public payload. No configs, credentials, memory or test binaries."""
import argparse
import gzip
import hashlib
import io
import re
import tarfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def package(archive):
    archive.parent.mkdir(parents=True, exist_ok=True)
    with archive.open("wb") as out, gzip.GzipFile(fileobj=out, filename="", mode="wb", mtime=0) as gz:
        with tarfile.open(fileobj=gz, mode="w|") as tar:
            for p in [ROOT / "nodepilot", *sorted((ROOT / "nodepilot_pkg").glob("*.py"))]:
                data = p.read_bytes().replace(b"\r\n", b"\n")
                info = tarfile.TarInfo(p.relative_to(ROOT).as_posix())
                info.size, info.mode, info.mtime = len(data), 0o600, 0
                tar.addfile(info, io.BytesIO(data))
    return hashlib.sha256(archive.read_bytes()).hexdigest()


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--archive", type=Path, default=ROOT / "dist/nodepilot-0.1.0.tar.gz")
    p.add_argument("--repo", help="发布仓库 owner/repo；只生成产物，不上传")
    p.add_argument("--tag", default="v0.1.0")
    args = p.parse_args()
    if args.repo and (not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", args.repo) or not re.fullmatch(r"v\d+\.\d+\.\d+", args.tag)):
        p.error("仓库/tag 格式错误")
    digest = package(args.archive)
    args.archive.with_suffix(args.archive.suffix + ".sha256").write_text(digest + "  " + args.archive.name + "\n")
    if args.repo:
        url = f"https://github.com/{args.repo}/releases/download/{args.tag}/{args.archive.name}"
        installer = (ROOT / "install.sh").read_text(encoding="utf-8")
        installer, urls = re.subn(r"^PAYLOAD_URL='[^']*'$", lambda m: f"PAYLOAD_URL='{url}'", installer, flags=re.MULTILINE)
        installer, hashes = re.subn(r"^PAYLOAD_SHA256='[^']*'$", lambda m: f"PAYLOAD_SHA256='{digest}'", installer, flags=re.MULTILINE)
        if urls != 1 or hashes != 1:
            raise SystemExit("安装器模板缺少唯一 URL/SHA-256 字段，拒绝生成发布文件")
        (args.archive.parent / "install.sh").write_text(installer, encoding="utf-8", newline="\n")
    print(f"项目包已生成，SHA-256：{digest}")


if __name__ == "__main__":
    main()
