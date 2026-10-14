"""Update only from the project's recorded GitHub release repository."""
import re
import subprocess
import tempfile
from pathlib import Path
from urllib.parse import urlsplit

from .common import PilotError, read_json, run
from .download import fetch
from .manager import OWNER


def online_update(tty_read, tty_write):
    root = Path("/opt/nodepilot")
    if root.is_symlink() or (root / ".owner").read_text() != OWNER:
        raise PilotError("项目安装所有权标记不匹配。")
    record = root / "source.json"
    if not record.exists():
        raise PilotError("本地源码安装尚未绑定发布仓库。请用新源码运行 bash install.sh --local --upgrade。")
    repo = read_json(record)["repository"]
    if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repo):
        raise PilotError("项目发布源无效。")
    with tempfile.TemporaryDirectory(prefix="nodepilot-update-") as directory:
        tmp = Path(directory)
        fetch(f"https://api.github.com/repos/{repo}/releases/latest", tmp / "release.json", limit=2 * 1024 * 1024)
        release = read_json(tmp / "release.json")
        if release.get("draft") or release.get("prerelease") or not re.fullmatch(r"v\d+\.\d+\.\d+", release["tag_name"]):
            raise PilotError("未找到支持的项目稳定版。")
        assets = [a for a in release["assets"] if a["name"] == "install.sh"]
        if len(assets) != 1:
            raise PilotError("项目发布缺少唯一 install.sh 附件。")
        asset = assets[0]
        url, digest = asset.get("browser_download_url", ""), asset.get("digest", "") or ""
        u = urlsplit(url)
        if u.hostname != "github.com" or not u.path.startswith(f"/{repo}/releases/download/") or not re.fullmatch(r"sha256:[0-9a-fA-F]{64}", digest):
            raise PilotError("项目安装器来源或官方 SHA-256 无效。")
        installer = tmp / "install.sh"
        fetch(url, installer, digest[7:], limit=1024 * 1024)
        run(["bash", "-n", installer])
        print(f"已校验 {release['tag_name']} 安装器，升级后进入新版本菜单。", file=tty_write, flush=True)
        status = subprocess.run(["bash", str(installer), "--upgrade"], stdin=tty_read,
                                stdout=tty_write, stderr=tty_write, check=False).returncode
        if status:
            raise PilotError(f"项目升级安装器返回 {status}；请检查终端错误，原节点保留。")
