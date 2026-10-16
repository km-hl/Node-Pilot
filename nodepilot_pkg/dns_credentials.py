"""Private Cloudflare Token profiles; tokens never become node metadata or argv."""
import json
import os
import re
import urllib.request
from pathlib import Path

from .common import PilotError, atomic_bytes, atomic_json, fsync_dir, private_dir, read_json
from .model import host

PROFILE = re.compile(r"cf-[0-9a-f]{16}\Z")


def validate_settings(settings):
    if not PROFILE.fullmatch(settings.get('credential_id', '')) or not re.fullmatch(r'[0-9a-fA-F]{32}', settings.get('zone_id', '')):
        raise PilotError('Cloudflare 凭据 ID / Zone ID 格式无效。')
    seconds = settings.get('propagation_seconds', 60)
    if type(seconds) is not int or not 10 <= seconds <= 600:
        raise PilotError('DNS 传播等待时间为 10–600 秒。')


def store_token(state, token, pending=False):
    """Low-level writer; interactive creation MUST use stage_token instead."""
    if not isinstance(token, str) or not re.fullmatch(r'[A-Za-z0-9_-]{20,256}', token):
        raise PilotError('请输入 Cloudflare API Token；不支持 Global API Key。')
    profile = 'cf-' + os.urandom(8).hex()
    directory = private_dir(Path(state)/'credentials')
    if pending:
        directory = private_dir(directory/'pending')
        atomic_json(directory/(profile+'.json'), {'schema':1,'owner':process_identity()})
    atomic_bytes(directory/(profile+'.ini'), ('dns_cloudflare_api_token = '+token+'\n').encode())
    return profile


def credential_path(state, profile, pending=True):
    if not isinstance(profile, str) or not PROFILE.fullmatch(profile):
        raise PilotError('非法 Cloudflare 凭据 ID。')
    directory, path = Path(state)/'credentials', Path(state)/'credentials'/(profile+'.ini')
    if directory.is_symlink():
        raise PilotError('凭据目录为外部链接。')
    staged = directory/'pending'/(profile+'.ini')
    if pending and (staged.exists() or staged.is_symlink()):
        if path.exists() or (directory/'pending').is_symlink():
            raise PilotError('Cloudflare 凭据状态冲突，拒绝使用。')
        directory, path = directory/'pending', staged
    if directory.is_symlink() or path.is_symlink() or not path.is_file():
        raise PilotError('Cloudflare 私有凭据文件缺失或为外部链接。')
    stat = path.stat()
    if stat.st_size > 1024 or stat.st_mode & 0o777 != 0o600 or (os.name == 'posix' and stat.st_uid != os.geteuid()):
        raise PilotError('Cloudflare 凭据必须为当前 root 所有的 0600 普通文件。')
    return path


def process_identity():
    # PID plus boot/start identity avoids deleting another live menu's credentials.
    try:
        return [os.getpid(), Path('/proc/sys/kernel/random/boot_id').read_text().strip(),
                Path('/proc/self/stat').read_text().rsplit(')',1)[1].split()[19]]
    except OSError:
        return [os.getpid(), '', '']


def owner_alive(owner):
    try:
        pid, boot, start = owner
        if type(pid) is not int or pid <= 0 or not boot or not start:
            raise ValueError()
        if Path('/proc/sys/kernel/random/boot_id').read_text().strip() != boot:
            return False
        return Path(f'/proc/{pid}/stat').read_text().rsplit(')',1)[1].split()[19] == start
    except FileNotFoundError:
        return False
    except (OSError, ValueError, TypeError, IndexError):
        raise PilotError('临时凭据所有者无法确认，拒绝自动清理。') from None


def stage_token(state, token, settings, domain):
    """Validate before writing, check provider without exposing errors or Token."""
    validate_settings({**settings,'credential_id':'cf-'+'0'*16})
    profile = store_token(state, token, pending=True)
    settings = {**settings,'credential_id':profile}
    try:
        verify_zone(state, settings, domain)
    except BaseException:
        discard_pending(state, profile)
        raise
    return settings


def profiles(value):
    found = set()
    if isinstance(value, dict):
        for key, item in value.items():
            if key == 'credential_id':
                if not isinstance(item,str) or not PROFILE.fullmatch(item):
                    raise PilotError('凭据引用损坏，拒绝清理。')
                found.add(item)
            else:
                found.update(profiles(item))
    elif isinstance(value,list):
        for item in value:
            found.update(profiles(item))
    return found


def commit_node(state, node):
    """Only called after native validation, service restart and listener success."""
    directory = Path(state)/'credentials'
    for profile in profiles(node):
        source = credential_path(state, profile)
        destination = directory/(profile+'.ini')
        if source != destination:
            os.replace(source,destination)
            fsync_dir(directory)
            (directory/'pending'/(profile+'.json')).unlink(missing_ok=True)
            fsync_dir(directory/'pending')


def references(state):
    """Conservative read-only scan: revisions, transactions, adoptions and backups.

    Retained backups/revisions intentionally pin credentials. Corruption or links
    make deletion unsafe, even when the damaged record looks unrelated.
    """
    state = Path(state)
    result = {}
    paths = []
    for name in ('nodes','adoptions','backups','deletions'):
        root = state/name
        if root.is_symlink():
            raise PilotError('引用目录为外部链接，拒绝清理凭据。')
        for path in root.rglob('*') if root.exists() else []:
            if path.is_symlink():
                if name=='nodes' and path.name=='current' and path.resolve().parent == path.parent/'revisions':
                    continue
                raise PilotError('引用目录存在未知链接，拒绝清理凭据。')
            if path.suffix=='.json' and path.is_file():
                paths.append(path)
    paths.extend(state.glob('*.json'))
    for path in paths:
        if path.is_symlink() or path.stat().st_size > 8*1024*1024:
            raise PilotError('引用记录不安全，拒绝清理凭据。')
        record=read_json(path)
        if not isinstance(record,dict) or record.get('schema',1) not in {1,2}:
            raise PilotError('引用记录 schema 不受支持，拒绝清理凭据。')
        for profile in profiles(record):
            result.setdefault(profile,[]).append(path.relative_to(state).as_posix())
    return result


def audit(state):
    refs = references(state)
    directory = Path(state)/'credentials'
    if directory.is_symlink():
        raise PilotError('凭据目录为外部链接。')
    records = []
    for path in sorted(directory.glob('*.ini')):
        credential_path(state,path.stem,pending=False)
        records.append({'id':path.stem,'references':refs.get(path.stem,[]),'orphan':path.stem not in refs})
    return records


def cleanup_orphans(state, selected, confirmed=False):
    if not confirmed:
        raise PilotError('正式凭据清理必须确认。')
    records = {r['id']:r for r in audit(state)}
    for profile in selected:
        if profile not in records or not records[profile]['orphan']:
            raise PilotError('凭据仍有引用或状态不明，拒绝删除。')
    for profile in selected:
        credential_path(state,profile,pending=False).unlink()
    if selected:
        fsync_dir(Path(state)/'credentials')


def discard_pending(state, profile):
    if not PROFILE.fullmatch(profile):
        raise PilotError('非法临时凭据 ID。')
    directory = Path(state)/'credentials'/'pending'
    if directory.is_symlink() or (Path(state)/'credentials').is_symlink():
        raise PilotError('临时凭据目录为外部链接。')
    for suffix in ('.ini','.json'):
        (directory/(profile+suffix)).unlink(missing_ok=True)
    if directory.exists():
        fsync_dir(directory)


def recover_pending(state, owned=(), only_owned=False):
    """Caller holds manager lock and recovers node/adoption transactions first."""
    directory = Path(state)/'credentials'/'pending'
    if directory.is_symlink():
        raise PilotError('临时凭据目录为外部链接。')
    paths = [p for p in sorted(directory.glob('*.ini')) if not only_owned or p.stem in owned]
    if not paths:
        return
    refs = references(state)
    # Only current/pending records may keep an uncommitted credential alive.
    for path in paths:
        profile = path.stem
        if not PROFILE.fullmatch(profile):
            raise PilotError('未知临时凭据，拒绝清理。')
        marker = directory/(profile+'.json')
        record = read_json(marker)
        if record.get('schema') != 1:
            raise PilotError('临时凭据 schema 不受支持。')
        if profile not in owned and owner_alive(record.get('owner')):
            continue
        live = [p for p in refs.get(profile,[]) if p.endswith('/pending.json') or p.startswith(('adoptions/','deletions/')) or '/' not in p]
        for current in (Path(state)/'nodes').glob('*/current/node.json'):
            if profile in profiles(read_json(current)):
                live.append(str(current))
        if live:
            raise PilotError('未提交凭据仍由当前节点/事务引用，请先恢复事务。')
        discard_pending(state,profile)


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


def verify_zone(state, settings, domain):
    validate_settings(settings)
    path = credential_path(state, settings['credential_id'])
    text = path.read_text().strip()
    if not re.fullmatch(r'dns_cloudflare_api_token = [A-Za-z0-9_-]{20,256}', text):
        raise PilotError('Cloudflare 凭据文件格式无效。')
    token = text.split(' = ', 1)[1]
    url = 'https://api.cloudflare.com/client/v4/zones/'+settings['zone_id']
    request = urllib.request.Request(url, headers={'Authorization':'Bearer '+token, 'User-Agent':'NodePilot'})
    try:
        with urllib.request.build_opener(NoRedirect()).open(request, timeout=10) as response:
            if response.geturl() != url:
                raise ValueError('redirect')
            data = response.read(65537)
        if len(data) > 65536:
            raise ValueError('size')
        record = json.loads(data)
        zone = host(record['result']['name'])
        if not record.get('success') or record['result']['id'].lower() != settings['zone_id'].lower():
            raise ValueError('zone')
        if domain != zone and not domain.endswith('.'+zone):
            raise ValueError('domain')
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        # Provider exception bodies can include credentials; never interpolate them.
        raise PilotError('Cloudflare Zone 验证失败；检查 Zone ID、域名及限定 Zone 的 DNS:Edit / Zone:Read 权限。') from None
    return path
