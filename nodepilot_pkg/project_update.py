"""Update only from the project's recorded GitHub release repository."""
import re
import subprocess
import tempfile
from pathlib import Path
from urllib.parse import urlsplit

from .common import PilotError, atomic_json, read_json, run
from .download import fetch
from .manager import LEGACY_OWNER, OWNER
from .releases import release as official_release, repository, stable_versions, version


def validate_source(record):
    if not isinstance(record, dict):
        raise PilotError('安装来源必须为结构化记录。')
    if set(record) == {'repository'}:  # Existing v0.3/RC records: keep authorized binding.
        repository(record['repository'])
        return record
    if (type(record.get('schema')) is not int or record['schema'] != 1 or not isinstance(record.get('provenance'),str)
            or record['provenance'] not in {'official-release','local-source','explicit-user-bound'}):
        raise PilotError('未知安装来源格式；保留记录并拒绝更新。')
    kind=record['provenance']
    allowed={'schema','provenance','previous_binding'} if kind=='local-source' else {'schema','provenance','repository'}
    if not set(record) <= allowed:
        raise PilotError('安装来源包含异常字段。')
    if kind!='local-source':repository(record.get('repository'))
    if 'previous_binding' in record:
        binding=record['previous_binding']
        if not isinstance(binding,dict) or set(binding)!={'repository'}:
            raise PilotError('历史更新源格式无效。')
        repository(binding['repository'])
    return record


def read_source(root):
    path=Path(root)/'source.json'
    if path.is_symlink():raise PilotError('安装来源为外部链接，拒绝读取。')
    if not path.exists():return None
    if not path.is_file() or path.stat().st_size>8192:raise PilotError('安装来源文件类型或大小异常。')
    return validate_source(read_json(path))


def active_repository(record):
    return record.get('repository') if record and record.get('provenance')!='local-source' else None


def planned_source(previous, source_url):
    """URL describes a payload already checked by the trusted installer, not trust by itself."""
    if source_url:
        url=urlsplit(source_url)
        match=re.fullmatch(r'/([A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+)/releases/download/v(\d+\.\d+\.\d+)/nodepilot-\2\.tar\.gz',url.path)
        if (url.scheme!='https' or url.netloc!='github.com' or not match or url.query or url.fragment):
            raise PilotError('Release 来源必须为已校验的 GitHub 稳定版附件 URL。')
        return {'schema':1,'provenance':'official-release','repository':repository(match[1])}
    if previous and previous.get('provenance')=='local-source':return previous
    record={'schema':1,'provenance':'local-source'}
    if active_repository(previous):record['previous_binding']={'repository':active_repository(previous)}
    return record


def source_label(record):
    if not record:return '来源未记录'
    repo=active_repository(record)
    return f"{record.get('provenance','legacy-bound')} / {repo}" if repo else '本地源码安装，未绑定在线更新源'


def authorize_source_change(previous, target, confirm, installed=False):
    changed=active_repository(previous)!=active_repository(target)
    if changed and (previous is not None or installed):
        if confirm is None or not confirm(previous,target):
            raise PilotError('安装来源变更未获确认；原绑定、配置与程序未改变。')


def release_asset(record, repo, name):
    assets=[a for a in record.get('assets',[]) if isinstance(a,dict) and a.get('name')==name]
    if len(assets)!=1:raise PilotError('项目发布缺少唯一的 '+name+' 附件。')
    asset=assets[0];url=asset.get('browser_download_url','');digest=asset.get('digest','') or ''
    if not isinstance(url,str) or not isinstance(digest,str):raise PilotError('项目附件元数据类型无效。')
    u=urlsplit(url)
    if (u.scheme!='https' or u.netloc!='github.com' or u.path!=f"/{repo}/releases/download/{record['tag_name']}/{name}"
            or u.query or u.fragment or not re.fullmatch(r'sha256:[0-9a-fA-F]{64}',digest)):
        raise PilotError('项目附件来源或官方 SHA-256 无效。')
    return asset


def bind_repository(repo, confirmed=False, root=Path('/opt/nodepilot')):
    """Caller holds the manager lock; confirmation authorizes this exact repository."""
    repo=repository(repo);root=Path(root)
    if confirmed is not True:raise PilotError('绑定需要明确授权：nodepilot bind-source owner/repo --confirm')
    if root.is_symlink() or (root/'.owner').is_symlink() or not (root/'.owner').is_file() or (root/'.owner').read_text() not in {OWNER,LEGACY_OWNER}:
        raise PilotError('项目安装所有权标记不匹配。')
    if (root/'program-pending.json').exists() or (root/'program-pending.json').is_symlink():
        raise PilotError('请先用可信新版安装器恢复活动程序事务，再绑定来源。')
    read_source(root)  # Corrupt existing evidence is never silently overwritten.
    release=official_release(repo)
    release_asset(release,repo,'install.sh')
    release_asset(release,repo,'nodepilot-'+release['tag_name'].lstrip('v')+'.tar.gz')
    atomic_json(root/'source.json',{'schema':1,'provenance':'explicit-user-bound','repository':repo})


def bound_repository(root=Path("/opt/nodepilot")):
    root = Path(root)
    if root.is_symlink() or (root / ".owner").is_symlink() or not (root / '.owner').is_file() or (root / ".owner").read_text() not in {OWNER,LEGACY_OWNER}:
        raise PilotError("项目安装所有权标记不匹配。")
    repo=active_repository(read_source(root))
    if not repo:
        raise PilotError('本地源码安装，未绑定在线更新源。使用可信源码 --local --upgrade，或审查来源后执行 nodepilot bind-source owner/repo --confirm。')
    return repo


def project_versions(root=Path("/opt/nodepilot")):
    # Earlier managers cannot recover adoption journals; avoid unsafe downgrades.
    return stable_versions(bound_repository(root), (0, 4, 0))


def check_update(root=Path("/opt/nodepilot")):
    return official_release(bound_repository(root))


def online_update(tty_read, tty_write, selected="latest"):
    repo = bound_repository()
    release = official_release(repo, selected)
    if version(release["tag_name"]) < (0, 4, 0):
        raise PilotError("v0.4.0 之前的管理器不能处理 DNS-01 和卸载事务，拒绝降级。")
    with tempfile.TemporaryDirectory(prefix="nodepilot-update-") as directory:
        tmp = Path(directory)
        if release.get("draft") or release.get("prerelease") or not re.fullmatch(r"v\d+\.\d+\.\d+", release["tag_name"]):
            raise PilotError("未找到支持的项目稳定版。")
        asset = release_asset(release,repo,'install.sh')
        url, digest = asset.get("browser_download_url", ""), asset.get("digest", "") or ""
        installer = tmp / "install.sh"
        fetch(url, installer, digest[7:], limit=1024 * 1024)
        run(["bash", "-n", installer])
        print(f"已校验 {release['tag_name']} 安装器，升级后进入新版本菜单。", file=tty_write, flush=True)
        status = subprocess.run(["bash", str(installer), "--upgrade"], stdin=tty_read,
                                stdout=tty_write, stderr=tty_write, check=False).returncode
        if status:
            raise PilotError(f"项目升级安装器返回 {status}；请检查终端错误，原节点保留。")
