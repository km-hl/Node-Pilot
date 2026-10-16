"""Bounded official stable-release discovery, exact version selection and comparison."""
import re
import tempfile
from pathlib import Path

from .common import PilotError, read_json
from .download import fetch

REPOSITORIES = {"xray": "XTLS/Xray-core", "sing-box": "SagerNet/sing-box", "caddy": "caddyserver/caddy"}
MINIMUM = {"xray": (25, 3, 6), "sing-box": (1, 12, 0), "caddy": (2, 7, 0)}


def version(value):
    if not isinstance(value, str) or len(value) > 32 or not re.fullmatch(r"v?\d+\.\d+\.\d+", value):
        raise PilotError("版本格式为 v1.2.3 / 1.2.3；仅支持官方稳定版。")
    return tuple(int(part) for part in value.lstrip("v").split("."))


def repository(value):
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", value) or any(p in {'.','..'} for p in value.split('/')):
        raise PilotError("发布仓库格式无效。")
    return value


def stable(record):
    if not isinstance(record, dict) or record.get("draft") or record.get("prerelease"):
        return False
    try:
        version(record["tag_name"])
        return True
    except (PilotError, KeyError):
        return False


def release(repo, selected="latest"):
    repository(repo)
    endpoint = "latest" if selected == "latest" else "tags/v" + ".".join(map(str, version(selected)))
    with tempfile.TemporaryDirectory(prefix="nodepilot-release-") as directory:
        path = Path(directory) / "release.json"
        fetch(f"https://api.github.com/repos/{repo}/releases/{endpoint}", path, limit=2 * 1024 * 1024)
        record = read_json(path)
    if not stable(record) or (selected != "latest" and version(record["tag_name"]) != version(selected)):
        raise PilotError("发布记录不是请求的官方稳定版本。")
    return record


def stable_versions(repo, minimum=(0, 0, 0)):
    repository(repo)
    tags = set()
    with tempfile.TemporaryDirectory(prefix="nodepilot-versions-") as directory:
        path = Path(directory) / "versions.json"
        # Asset metadata is large. Parse at most five releases at a time rather
        # than retaining thirty complete asset catalogs on a 256MiB host.
        for page in range(1, 7):
            fetch(f"https://api.github.com/repos/{repo}/releases?per_page=5&page={page}", path, limit=4 * 1024 * 1024)
            records = read_json(path)
            if not isinstance(records, list) or len(records) > 5:
                raise PilotError("稳定版本列表无效。")
            tags.update(r["tag_name"] for r in records if stable(r) and version(r["tag_name"]) >= minimum)
            count = len(records)
            del records
            if count < 5:
                break
    return sorted(tags, key=version, reverse=True)
