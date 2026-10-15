"""Local certificates and isolated certbot HTTP-01 lineages; no curl|sh ACME."""
import ipaddress
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path

from .common import PilotError, atomic_bytes, atomic_json, private_dir, read_json, run
from .system import dns_matches, http01_bindings, port_free, public_ips


def validate_pair(certificate, private_key, sni):
    cert, key = Path(certificate), Path(private_key)
    if not cert.is_file() or not key.is_file() or cert.stat().st_size > 256 * 1024 or key.stat().st_size > 64 * 1024:
        raise PilotError("证书/私钥不存在或大小异常。")
    run(["openssl", "x509", "-in", cert, "-noout", "-checkend", "0"])
    check = "-checkip" if is_ip(sni) else "-checkhost"
    output = run(["openssl", "x509", "-in", cert, "-noout", check, sni])
    if "does NOT match" in output:
        raise PilotError("SNI 与证书 SAN 不匹配。")
    cert_pub = run(["openssl", "x509", "-in", cert, "-pubkey", "-noout"])
    key_pub = run(["openssl", "pkey", "-in", key, "-pubout", "-passin", "pass:"])
    if cert_pub != key_pub:
        raise PilotError("证书与私钥不匹配；只支持无口令服务端私钥。")


def is_ip(value):
    try:
        ipaddress.ip_address(value)
        return True
    except ValueError:
        return False


def acme(n, renew=False, state=Path("/etc/nodepilot")):
    tls = n["tls"]
    domain = tls.get("domain", n["sni"])
    if is_ip(domain) or domain != n["sni"]:
        raise PilotError("ACME 必须使用域名，且证书域名与 SNI 相同。")
    challenge = tls.get('challenge', 'http-01')
    if challenge == 'dns-cloudflare':
        from .dns_credentials import verify_zone
        credential = verify_zone(state, tls['dns'], domain)
        authenticator = ['--dns-cloudflare', '--dns-cloudflare-credentials', credential,
                         '--dns-cloudflare-propagation-seconds', str(tls['dns'].get('propagation_seconds',60))]
    elif challenge == 'http-01':
        bindings = http01_bindings(dns_matches(domain, public_ips()))
        for address in bindings:
            port_free(address, 80, "tcp", v6only=True)
        if len(bindings) != 1:
            raise PilotError('Certbot standalone 双栈监听尚不能可靠保证；请选择 Cloudflare DNS-01，或使用显式分族监听的 Caddy。未修改 IPv6/现有服务。')
        authenticator = ['--standalone', '--preferred-challenges', 'http', '--http-01-port', '80', '--http-01-address', bindings[-1]]
    else:
        raise PilotError('不支持的 ACME 挑战方式。')
    if not shutil.which("certbot"):
        raise PilotError("请先安装官方发行版 certbot 包：apt-get install certbot。")
    # Isolated config/work/log directories: never touch another certbot installation.
    base = Path(state) / "acme" / n["id"]
    from .common import private_dir
    private_dir(base)
    config_file=base/'cli.ini'
    if config_file.is_symlink():raise PilotError('拒绝外部 certbot 配置链接。')
    atomic_bytes(config_file,b'# NodePilot isolated certbot settings\n')
    isolated=['--config',config_file,'--config-dir',base/'config','--work-dir',base/'work','--logs-dir',base/'logs']
    if challenge=='dns-cloudflare':
        plugins=run(['certbot','plugins',*isolated],timeout=20)
        if 'dns-cloudflare' not in plugins:raise PilotError('请安装发行版 python3-certbot-dns-cloudflare 插件。')
    email = tls.get("email", "")
    from .model import email as validate_email
    validate_email(email)
    contact = ["--email", email] if email else ["--register-unsafely-without-email"]
    command=[sys.executable,Path(__file__).with_name('acme_runner.py')] if challenge=='dns-cloudflare' else ['certbot']
    run([*command, "certonly", *authenticator, "--non-interactive", "--agree-tos", *contact,*isolated,
         "--server", "https://acme-v02.api.letsencrypt.org/directory", "--cert-name", n["id"],
         "--force-renewal" if renew else "--keep-until-expiring", "-d", domain],
         timeout=240+(tls.get('dns',{}).get('propagation_seconds',60) if challenge=='dns-cloudflare' else 0),
         graceful_interrupt=challenge=='dns-cloudflare')
    lineage = base / "config/live" / n["id"]
    return lineage / "fullchain.pem", lineage / "privkey.pem"


def event(state, n, status):
    directory = private_dir(Path(state)/'certificate-status')
    atomic_json(directory/(n['id']+'.json'), {'sni':n['sni'], 'status':status, 'at':datetime.now(timezone.utc).isoformat()})


def reusable(n, seconds=30 * 86400):
    try:
        validate_pair(n["tls"]["certificate"], n["tls"]["key"], n["sni"])
        run(["openssl", "x509", "-in", n["tls"]["certificate"], "-noout", "-checkend", str(seconds)])
        return True
    except (PilotError, OSError):
        return False


def prepare(n, revision, renew=False, state=Path("/etc/nodepilot")):
    tls = n["tls"]
    certificate, key = revision / "certificate.pem", revision / "key.pem"
    if tls["mode"] == "selfsigned" and (renew or not Path(tls["certificate"]).is_file()):
        san = ("IP:" if is_ip(n["sni"]) else "DNS:") + n["sni"]
        run(["openssl", "req", "-x509", "-newkey", "ec", "-pkeyopt", "ec_paramgen_curve:P-256",
             "-nodes", "-days", "30", "-subj", "/CN=NodePilot-test", "-addext", "subjectAltName=" + san,
             "-keyout", key, "-out", certificate])
        certificate.chmod(0o600)
        key.chmod(0o600)
    else:
        if tls["mode"] == "acme" and (renew or not reusable(n)):
            try:
                source_cert, source_key = acme(n, renew=renew, state=state)
                validate_pair(source_cert, source_key, n['sni'])
                event(state, n, '签发/续期成功，等待配置切换')
            except (PilotError, OSError):
                event(state, n, '签发/续期失败，保留原修订')
                raise
        else:
            source_cert, source_key = Path(tls["certificate"]), Path(tls["key"])
        validate_pair(source_cert, source_key, n["sni"])
        atomic_bytes(certificate, source_cert.read_bytes())
        atomic_bytes(key, source_key.read_bytes())
    validate_pair(certificate, key, n["sni"])
    tls["certificate"], tls["key"] = str(certificate), str(key)


def due(n):
    if n['protocol'] == 'reality':
        if n.get('caddy',{}).get('certificate_mode') != 'dns01':
            return False
        n = holder(n)
    if n["tls"]["mode"] != "acme":
        return False
    try:
        run(["openssl", "x509", "-in", n["tls"]["certificate"], "-noout", "-checkend", str(30 * 86400)])
        return False
    except PilotError:
        return True


def holder(n):
    c = n['caddy']
    return {'id':n['id'], 'sni':n['sni'], 'protocol':'anytls', 'tls':{
        'mode':'acme' if c['certificate_mode']=='dns01' else c['certificate_mode'],
        'challenge':'dns-cloudflare', 'domain':n['sni'], 'email':c.get('email',''), 'dns':c.get('dns',{}),
        'certificate':c.get('certificate','/nonexistent'), 'key':c.get('key','/nonexistent')}}


def information(n, state=Path('/etc/nodepilot'), files=None):
    if n['protocol'] == 'reality':
        if n.get('reality',{}).get('mode') != 'self':
            return None
        c = n['caddy']
        certificate = files['certificate.pem'] if files else c.get('certificate')
        automatic = c['certificate_mode'] in {'acme','dns01'}
    else:
        certificate = n['tls']['certificate']
        automatic = n['tls']['mode']=='acme'
    result = {'domain':n['sni'], 'automatic':automatic, 'status':'未验证', 'issuer':'未知', 'expires':'未知', 'days':None}
    try:
        output = run(['openssl','x509','-in',certificate,'-noout','-issuer','-enddate','-nameopt','RFC2253'])
        fields = dict(line.split('=',1) for line in output.splitlines() if '=' in line)
        date = datetime.strptime(fields['notAfter'], '%b %d %H:%M:%S %Y %Z').replace(tzinfo=timezone.utc)
        result.update(issuer=fields['issuer'], expires=date.isoformat(), days=int((date-datetime.now(timezone.utc)).total_seconds()//86400))
        result['status'] = '已过期' if result['days'] < 0 else ('临期' if result['days'] < 30 else '有效')
    except (PilotError, TypeError, KeyError, ValueError):
        pass
    path = Path(state)/'certificate-status'/(n['id']+'.json')
    result['last_renewal'] = read_json(path) if path.is_file() else {'status':'暂无记录'}
    if n['protocol']=='reality' and n['caddy']['certificate_mode']=='acme' and certificate:
        try:
            changed=datetime.fromtimestamp(Path(certificate).stat().st_mtime,timezone.utc).isoformat()
            result['last_renewal']={'status':'由 Caddy 管理；证书文件最近更新 '+changed+'（不是签发日志）'}
        except OSError:
            pass
    if automatic:
        try:
            unit='nodepilot-caddy.service' if n['protocol']=='reality' and n['caddy']['certificate_mode']=='acme' else 'nodepilot-renew.timer'
            result['automatic_enabled']=run(['systemctl','is-active',unit])=='active'
        except PilotError:
            result['automatic_enabled']=False
    else:
        result['automatic_enabled']=False
    settings = n.get('caddy',{}).get('dns') if n['protocol']=='reality' else n.get('tls',{}).get('dns')
    result['dns_credentials'] = '不适用'
    if automatic and settings:
        from .dns_credentials import credential_path
        try:
            credential_path(state, settings['credential_id'], pending=False)
            result['dns_credentials'] = '已绑定（权限仍需续期时验证）'
        except (PilotError, KeyError):
            result['dns_credentials'] = 'DNS 凭据缺失；请重新绑定 Token'
    result['renewal_failed'] = '失败' in result['last_renewal'].get('status','')
    return result
