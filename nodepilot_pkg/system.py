import ipaddress
import platform
import re
import shutil
import socket
import ssl
import tempfile
from pathlib import Path

from .common import PilotError, root_required, run
from .download import fetch
from .model import host, split_target


def supported():
    root_required()
    values = {}
    for line in Path("/etc/os-release").read_text().splitlines():
        if "=" in line:
            k, v = line.split("=", 1)
            values[k] = v.strip('"')
    if (values.get("ID"), values.get("VERSION_ID")) not in {
        ("debian", "12"), ("debian", "13"), ("ubuntu", "22.04"), ("ubuntu", "24.04")}:
        raise PilotError("支持 Debian 12/13、Ubuntu 22.04/24.04。")
    if not Path("/run/systemd/system").is_dir():
        raise PilotError("需要 systemd 正在运行；容器/离线环境仅支持配置测试。")
    architecture()
    return values.get("PRETTY_NAME", values["ID"])


def architecture():
    arch = {"x86_64": "amd64", "aarch64": "arm64", "AMD64": "amd64"}.get(platform.machine())
    if arch is None:
        raise PilotError("只支持 amd64/arm64。")
    return arch


def existing_engines(proc=Path("/proc")):
    """Read process names only: include panel-bundled cores, never read argv/secrets."""
    found = []
    try:
        processes = list(Path(proc).iterdir())
    except OSError:
        return found
    for process in processes:
        if not process.name.isdigit():
            continue
        try:
            name = (process / "comm").read_text(errors="replace").strip()
            if not name.startswith(("xray", "sing-box", "singbox")):
                continue
            import os
            executable = os.readlink(process / "exe")
            if executable.startswith("/opt/nodepilot/cores/"):
                continue
            service = ""
            for line in (process / "cgroup").read_text(errors="replace").splitlines():
                for part in line.split("/"):
                    if part.endswith(".service"):
                        service = part
            clean = lambda text: "".join(c if c.isprintable() else "?" for c in text)[:80]
            found.append({"name": clean(Path(executable).name), "pid": int(process.name), "service": clean(service)})
        except OSError:
            continue
    return found


def public_ips():
    found = {"ipv4": "", "ipv6": ""}
    with tempfile.TemporaryDirectory(prefix="nodepilot-ip-") as d:
        for family, urls in {"ipv4": ["https://api4.ipify.org", "https://ipv4.icanhazip.com"],
                             "ipv6": ["https://api6.ipify.org", "https://ipv6.icanhazip.com"]}.items():
            for url in urls:
                try:
                    # Single short attempt: the menu must not wait through long retries.
                    import urllib.request
                    with urllib.request.urlopen(url, timeout=3) as response:
                        value = response.read(128).decode().strip()
                    ip = ipaddress.ip_address(value)
                    if ip.is_global and ip.version == (4 if family == "ipv4" else 6):
                        found[family] = str(ip)
                        break
                except (OSError, ValueError):
                    continue
    return found


def port_free(listen, port, transport):
    # TCP cores reuse TIME_WAIT addresses. listen() still rejects live listeners.
    # UDP must not enable reuse: it could hide a live QUIC listener.
    address = ipaddress.ip_address(listen)
    family = socket.AF_INET6 if address.version == 6 else socket.AF_INET
    kind = socket.SOCK_DGRAM if transport == "udp" else socket.SOCK_STREAM
    try:
        with socket.socket(family, kind) as sock:
            if kind == socket.SOCK_STREAM:
                sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            if family == socket.AF_INET6:
                sock.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_V6ONLY, 0)
            sock.bind((listen, port))
            if kind == socket.SOCK_STREAM:
                sock.listen(1)
    except OSError as e:
        raise PilotError(f"{listen}:{port}/{transport} 无法绑定：端口占用或地址不可用。") from e


def same_binding(a, b):
    return all(a.get(k) == b.get(k) for k in ("listen", "port", "transport"))


def suggested_listen(server, ips):
    try:
        if ipaddress.ip_address(server).version == 6:
            return "::"
    except ValueError:
        pass
    return "::" if ips.get("ipv6") and not ips.get("ipv4") else "0.0.0.0"


def conflicts(a, b):
    if a["transport"] != b["transport"] or a["port"] != b["port"]:
        return False
    x, y = ipaddress.ip_address(a["listen"]), ipaddress.ip_address(b["listen"])
    return x == y or x.is_unspecified or y.is_unspecified


def probe_reality(target, sni, proxy=False, cafile=None, timeout=8):
    h, p = split_target(target)
    context = ssl.create_default_context(cafile=cafile)
    context.minimum_version = ssl.TLSVersion.TLSv1_3
    context.set_alpn_protocols(["h2"])
    try:
        with socket.create_connection((h, p), timeout=timeout) as sock:
            if proxy:
                sock.sendall(f"PROXY TCP4 127.0.0.1 127.0.0.1 1 {p}\r\n".encode())
            with context.wrap_socket(sock, server_hostname=sni) as tls:
                if tls.version() != "TLSv1.3" or tls.selected_alpn_protocol() != "h2":
                    raise PilotError("Reality 目标必须通过 TLS 1.3 / h2 探测；请另选目标。")
    except (OSError, ssl.SSLError) as e:
        raise PilotError("Reality 目标 TLS/SNI 探测失败：检查证书 SAN、目标端口和出站连接。") from e


def dns_matches(domain, ips):
    host(domain)
    try:
        answers = {x[4][0] for x in socket.getaddrinfo(domain, None, type=socket.SOCK_STREAM)}
    except OSError as e:
        raise PilotError("无法解析域名 DNS。") from e
    expected = {ip for ip in ips.values() if ip}
    if not answers or not expected or not answers.issubset(expected):
        raise PilotError("域名全部 A/AAAA 必须指向已探测的本机公网 IP；请检查代理/CDN、旧 AAAA 和 DNS 传播。")


def diagnostics(n=None):
    lines = ["防火墙只读诊断；云安全组和外部 UDP 可达性需另行验证。"]
    commands = [["ss", "-lntup"], ["free", "-m"]]
    if shutil.which("ufw"):
        commands.append(["ufw", "status", "verbose"])
    if shutil.which("nft"):
        commands.append(["nft", "list", "ruleset"])
    for args in commands:
        try:
            lines.append(run(args, timeout=10))
        except PilotError as e:
            lines.append(str(e))
    if n:
        lines.append(f"请在主机防火墙和云安全组放行 {n['port']}/{n['transport']}。本程序未修改规则。")
    return "\n".join(lines)
