"""HTTPS streaming downloads, retry, bounds, and mandatory SHA-256 verification."""
import hashlib
import json
import os
import re
import time
import urllib.error
import urllib.request
from pathlib import Path
from urllib.parse import urlsplit

from .common import PilotError


def https(url):
    u = urlsplit(url)
    if u.scheme != "https" or not u.hostname or u.username or u.password:
        raise PilotError("下载源必须是无凭据的 HTTPS URL。")
    return url


class HTTPSRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        https(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def fetch(url, path, expected=None, limit=160 * 1024 * 1024):
    https(url)
    if expected is not None and not re.fullmatch(r"[0-9a-fA-F]{64}", expected):
        raise PilotError("缺少合法 SHA-256，拒绝下载执行。")
    path = Path(path)
    opener = urllib.request.build_opener(HTTPSRedirect())
    for attempt in range(3):
        try:
            request = urllib.request.Request(url, headers={"User-Agent": "NodePilot/0.1", "Accept": "application/vnd.github+json"})
            with opener.open(request, timeout=20) as response, path.open("wb") as out:
                https(response.geturl())
                os.chmod(path, 0o600)
                total, digest = 0, hashlib.sha256()
                start = time.monotonic()
                while True:
                    block = response.read(256 * 1024)
                    if not block:
                        break
                    total += len(block)
                    if total > limit or time.monotonic() - start > 300:
                        raise PilotError("下载超出大小/总时间上限。")
                    out.write(block)
                    digest.update(block)
                out.flush()
                os.fsync(out.fileno())
            if not total:
                raise PilotError("下载内容为空。")
            if expected and digest.hexdigest().lower() != expected.lower():
                raise PilotError("SHA-256 校验失败，文件已删除，拒绝执行。")
            return digest.hexdigest()
        except PilotError:
            path.unlink(missing_ok=True)
            raise
        except (OSError, urllib.error.URLError) as e:
            path.unlink(missing_ok=True)
            if attempt == 2:
                raise PilotError("HTTPS 下载失败（网络、TLS、限流或超时）；请稍后重试。") from e
            time.sleep(attempt + 1)


def github_release(repo, directory):
    if repo not in {"XTLS/Xray-core", "SagerNet/sing-box", "caddyserver/caddy"}:
        raise PilotError("内核必须来自指定官方仓库。")
    path = Path(directory) / "release.json"
    fetch(f"https://api.github.com/repos/{repo}/releases/latest", path, limit=2 * 1024 * 1024)
    try:
        release = json.loads(path.read_text())
        if release.get("draft") or release.get("prerelease") or not re.fullmatch(r"v?\d+\.\d+\.\d+", release["tag_name"]):
            raise ValueError()
        return release
    except (KeyError, ValueError, TypeError) as e:
        raise PilotError("官方发布元数据不是支持的稳定版本。") from e


def verified_asset(asset, directory):
    url = asset.get("browser_download_url", "")
    u = urlsplit(url)
    if u.hostname != "github.com" or not u.path.startswith(("/XTLS/Xray-core/releases/download/", "/SagerNet/sing-box/releases/download/", "/caddyserver/caddy/releases/download/")):
        raise PilotError("发布附件不属于官方来源。")
    digest = asset.get("digest", "") or ""
    if not re.fullmatch(r"sha256:[0-9a-fA-F]{64}", digest):
        raise PilotError("官方 API 未提供 SHA-256；安全中止，不运行未校验附件。")
    path = Path(directory) / "archive"
    mirror = os.environ.get("NODEPILOT_MIRROR", "")
    try:
        fetch(url, path, digest[7:])
    except PilotError:
        if not mirror:
            raise
        # Mirror only changes transport; expected digest always comes from official API.
        fetch(https(mirror.rstrip("/") + "/" + url), path, digest[7:])
    return path
