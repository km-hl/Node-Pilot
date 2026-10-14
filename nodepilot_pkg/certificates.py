"""Local certificates and isolated certbot HTTP-01 lineages; no curl|sh ACME."""
import ipaddress
import shutil
from pathlib import Path

from .common import PilotError, atomic_bytes, run
from .system import dns_matches, port_free, public_ips


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


def acme(n):
    tls = n["tls"]
    domain = tls.get("domain", n["sni"])
    if is_ip(domain) or domain != n["sni"]:
        raise PilotError("HTTP-01 必须使用域名，且 ACME 域名与 SNI 相同。")
    dns_matches(domain, public_ips())
    port_free("0.0.0.0", 80, "tcp")
    port_free("::", 80, "tcp")
    if not shutil.which("certbot"):
        raise PilotError("请先安装官方发行版 certbot 包：apt-get install certbot。")
    # Isolated config/work/log directories: never touch another certbot installation.
    base = Path("/etc/nodepilot/acme") / n["id"]
    from .common import private_dir
    private_dir(base)
    email = tls.get("email", "")
    if "@" not in email or any(c.isspace() for c in email):
        raise PilotError("请输入有效 ACME 联系邮箱。")
    run(["certbot", "certonly", "--standalone", "--preferred-challenges", "http",
         "--http-01-port", "80", "--non-interactive", "--agree-tos", "--email", email,
         "--config-dir", base / "config", "--work-dir", base / "work", "--logs-dir", base / "logs",
         "--server", "https://acme-v02.api.letsencrypt.org/directory", "--cert-name", n["id"],
         "--keep-until-expiring", "-d", domain], timeout=240)
    lineage = base / "config/live" / n["id"]
    return lineage / "fullchain.pem", lineage / "privkey.pem"


def prepare(n, revision, renew=False):
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
        if tls["mode"] == "acme":
            source_cert, source_key = acme(n)
        else:
            source_cert, source_key = Path(tls["certificate"]), Path(tls["key"])
        validate_pair(source_cert, source_key, n["sni"])
        atomic_bytes(certificate, source_cert.read_bytes())
        atomic_bytes(key, source_key.read_bytes())
    validate_pair(certificate, key, n["sni"])
    tls["certificate"], tls["key"] = str(certificate), str(key)


def due(n):
    if n["protocol"] == "reality" or n["tls"]["mode"] != "acme":
        return False
    try:
        run(["openssl", "x509", "-in", n["tls"]["certificate"], "-noout", "-checkend", str(30 * 86400)])
        return False
    except PilotError:
        return True
