"""Official verified core installs isolated from all system-installed cores."""
import base64
import os
import re
import shutil
import struct
import tarfile
import tempfile
import zipfile
from pathlib import Path

from .common import PilotError, atomic_json, fsync_dir, private_dir, run, run_bytes
from .download import github_release, verified_asset
from .system import architecture


def verify_elf(path, arch):
    with Path(path).open("rb") as f:
        h = f.read(20)
    expected = 62 if arch == "amd64" else 183
    if len(h) < 20 or h[:4] != b"\x7fELF" or h[4:6] != b"\x02\x01" or struct.unpack("<H", h[18:20])[0] != expected:
        raise PilotError("内核不是目标架构的 64 位小端 ELF。")


def extract_core(archive, output, core):
    # Never extractall: only a bounded regular core file, no paths/links from archives.
    if core == "xray":
        with zipfile.ZipFile(archive) as z:
            entries = [i for i in z.infolist() if i.filename == "xray"]
            if len(entries) != 1 or entries[0].file_size > 180 * 1024 * 1024:
                raise PilotError("Xray 附件中缺少唯一、合理大小的内核。")
            with z.open(entries[0]) as src, Path(output).open("wb") as dst:
                shutil.copyfileobj(src, dst, 256 * 1024)
    else:
        with tarfile.open(archive, "r:gz") as t:
            entries = [m for m in t if m.name.endswith("/sing-box") and m.isfile()]
            if len(entries) != 1 or entries[0].size > 180 * 1024 * 1024:
                raise PilotError("sing-box 附件中缺少唯一、合理大小的内核。")
            with t.extractfile(entries[0]) as src, Path(output).open("wb") as dst:
                shutil.copyfileobj(src, dst, 256 * 1024)


class Cores:
    def __init__(self, root=Path("/opt/nodepilot/cores")):
        self.root = Path(root)

    def binary(self, core):
        if core not in {"xray", "sing-box"}:
            raise PilotError("非法内核。")
        path = self.root / core / "current" / core
        if not path.is_file() or not (path.parent / "verified.json").is_file():
            raise PilotError(f"{core} 未安装；请先在菜单安装/更新官方内核。")
        return path

    def stage(self, core):
        arch = architecture()
        parent = private_dir(self.root / core)
        repo = "XTLS/Xray-core" if core == "xray" else "SagerNet/sing-box"
        with tempfile.TemporaryDirectory(prefix="download-", dir=parent) as tmp:
            release = github_release(repo, tmp)
            version = release["tag_name"].lstrip("v")
            if core == "sing-box" and tuple(map(int, version.split("."))) < (1, 12, 0):
                raise PilotError("AnyTLS 需要 sing-box >= 1.12.0。")
            name = (f"Xray-linux-{'64' if arch == 'amd64' else 'arm64-v8a'}.zip" if core == "xray"
                    else f"sing-box-{version}-linux-{arch}.tar.gz")
            assets = [a for a in release["assets"] if a["name"] == name]
            if len(assets) != 1:
                raise PilotError(f"稳定版未提供架构对应附件：{name}")
            archive = verified_asset(assets[0], tmp)
            candidate = Path(tmp) / core
            extract_core(archive, candidate, core)
            verify_elf(candidate, arch)
            candidate.chmod(0o700)
            output = run([candidate, "version"], timeout=15)
            if not re.search(r"(?<![\d.])" + re.escape(version) + r"(?![\d.])", output):
                raise PilotError("二进制报告版本与官方发布版本不一致。")
            destination = parent / (version + "-" + os.urandom(4).hex())
            private_dir(destination)
            shutil.copy2(candidate, destination / core)
            atomic_json(destination / "verified.json", {"version": version, "architecture": arch,
                        "source": assets[0]["browser_download_url"], "digest": assets[0]["digest"]})
            return destination

    def switch(self, core, destination):
        parent = self.root / core
        temp = parent / ".current-new"
        temp.unlink(missing_ok=True)
        temp.symlink_to(Path(destination).relative_to(parent), target_is_directory=True)
        os.replace(temp, parent / "current")
        fsync_dir(parent)

    def check(self, n, path, binary=None):
        b = binary or self.binary(n["core"])
        args = [b, "run", "-test", "-config", path] if n["core"] == "xray" else [b, "check", "-c", path]
        run(args, timeout=30)

    def keypair(self, private=None):
        from .model import key
        if private:
            key(private)
            # RFC 8410 X25519 PKCS#8/SPKI: derive the public key through stdin,
            # rather than exposing the private key in /proc/<pid>/cmdline (-i).
            der = bytes.fromhex("302e020100300506032b656e04220420") + base64.urlsafe_b64decode(private + "=")
            public = run_bytes(["openssl", "pkey", "-inform", "DER", "-pubout", "-outform", "DER"], der)
            prefix = bytes.fromhex("302a300506032b656e032100")
            if len(public) != len(prefix) + 32 or not public.startswith(prefix):
                raise PilotError("无法解析 OpenSSL X25519 公钥。")
            return private, key(base64.urlsafe_b64encode(public[len(prefix):]).decode().rstrip("="))
        args = [self.binary("xray"), "x25519"]
        result = run(args)
        fields = {re.sub(r"[^a-z]", "", k.lower()): v.strip() for k, v in
                  re.findall(r"^([^:\n]+):\s*([A-Za-z0-9_-]+)\s*$", result, re.MULTILINE)}
        # Recent Xray labels the public key Password; older versions use Public key.
        a = fields.get("privatekey")
        b = fields.get("publickey") or fields.get("passwordpublickey") or fields.get("password")
        if not a or not b:
            raise PilotError("无法解析 Xray x25519 输出。")
        return key(a), key(b)
