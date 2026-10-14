"""Private Cloudflare Token profiles; tokens never become node metadata or argv."""
import json
import os
import re
import urllib.request
from pathlib import Path

from .common import PilotError, atomic_bytes, private_dir
from .model import host

PROFILE = re.compile(r"cf-[0-9a-f]{16}\Z")


def validate_settings(settings):
    if not PROFILE.fullmatch(settings.get('credential_id', '')) or not re.fullmatch(r'[0-9a-fA-F]{32}', settings.get('zone_id', '')):
        raise PilotError('Cloudflare 凭据 ID / Zone ID 格式无效。')
    seconds = settings.get('propagation_seconds', 60)
    if type(seconds) is not int or not 10 <= seconds <= 600:
        raise PilotError('DNS 传播等待时间为 10–600 秒。')


def store_token(state, token):
    if not isinstance(token, str) or not re.fullmatch(r'[A-Za-z0-9_-]{20,256}', token):
        raise PilotError('请输入 Cloudflare API Token；不支持 Global API Key。')
    profile = 'cf-' + os.urandom(8).hex()
    directory = private_dir(Path(state)/'credentials')
    atomic_bytes(directory/(profile+'.ini'), ('dns_cloudflare_api_token = '+token+'\n').encode())
    return profile


def credential_path(state, profile):
    if not isinstance(profile, str) or not PROFILE.fullmatch(profile):
        raise PilotError('非法 Cloudflare 凭据 ID。')
    directory, path = Path(state)/'credentials', Path(state)/'credentials'/(profile+'.ini')
    if directory.is_symlink() or path.is_symlink() or not path.is_file():
        raise PilotError('Cloudflare 私有凭据文件缺失或为外部链接。')
    stat = path.stat()
    if stat.st_size > 1024 or stat.st_mode & 0o777 != 0o600 or (os.name == 'posix' and stat.st_uid != os.geteuid()):
        raise PilotError('Cloudflare 凭据必须为当前 root 所有的 0600 普通文件。')
    return path


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
    except (OSError, ValueError, KeyError, TypeError) as error:
        # Provider exception bodies can include credentials; never interpolate them.
        raise PilotError('Cloudflare Zone 验证失败；检查 Zone ID、域名及限定 Zone 的 DNS:Edit / Zone:Read 权限。') from None
    return path
