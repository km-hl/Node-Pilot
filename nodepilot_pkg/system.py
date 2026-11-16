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


def port_free(listen, port, transport, v6only=False):
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
                sock.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_V6ONLY, int(v6only))
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


def probe_reality(target, sni, cafile=None, timeout=8):
    h, p = split_target(target)
    context = ssl.create_default_context(cafile=cafile)
    context.minimum_version = ssl.TLSVersion.TLSv1_3
    context.set_alpn_protocols(["h2"])
    try:
        with socket.create_connection((h, p), timeout=timeout) as sock:
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
    if any(not ipaddress.ip_address(ip).is_global for ip in answers):
        raise PilotError("HTTP-01 域名必须解析到公网 IP。")
    return answers


def http01_bindings(answers):
    """Check only families actually published by this domain; no global changes."""
    versions = {ipaddress.ip_address(ip).version for ip in answers}
    if not versions:
        raise PilotError("HTTP-01 没有可验证的 DNS 地址。")
    return [address for version, address in ((4, "0.0.0.0"), (6, "::")) if version in versions]


def http01_dual_stack_ready(bindv6only_path=Path("/proc/sys/net/ipv6/bindv6only")):
    """Verify kernel socket capability for dual-stack Certbot HTTP-01 without mutating sysctl."""
    if not socket.has_ipv6:
        raise PilotError("当前 Python/内核不支持 IPv6 套接字，无法执行双栈 HTTP-01；请改用 DNS-01 或 Caddy。")
    try:
        with socket.socket(socket.AF_INET6, socket.SOCK_STREAM) as probe:
            probe.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_V6ONLY, 0)
    except OSError as error:
        raise PilotError("当前系统禁用 IPv6 双栈套接字；请改用 Cloudflare DNS-01 或 Caddy。") from error
    try:
        value = bindv6only_path.read_text().strip()
        if value not in {"0", "1"}:
            raise PilotError("无法确认 net.ipv6.bindv6only 状态；请选择 DNS-01 或 Caddy。")
    except OSError:
        pass
    return True


def available_memory(path=Path('/proc/meminfo')):
    """Read-only estimate; no swap/sysctl changes and no hardware guarantee."""
    try:
        values = dict(re.findall(r'^(MemTotal|MemAvailable):\s+(\d+) kB$', path.read_text(), re.M))
        return {k: int(v)//1024 for k, v in values.items()}
    except (OSError, ValueError):
        return {}


def firewall_reference(n):
    """Build copyable non-mutating firewall/NAT reference rules for a single node."""
    port, proto = n["port"], n["transport"]
    lines = [
        f"请在主机防火墙和云安全组放行 {port}/{proto}（本程序未自动修改任何规则）。",
        f"  ufw 参考：ufw allow {port}/{proto}",
        f"  iptables 参考：iptables -I INPUT -p {proto} --dport {port} -j ACCEPT",
        f"  nftables 参考：nft add rule inet filter input {proto} dport {port} accept",
    ]
    if n.get("mport") and proto == "udp":
        mport_iptables = n["mport"].replace("-", ":").split(",")[0]
        lines.append(f"  HY2 端口跳跃参考（需同步在云安全组放行 UDP {n['mport']}）：")
        lines.append(f"    iptables -t nat -A PREROUTING -p udp --dport {mport_iptables} -j REDIRECT --to-ports {port}")
        if ":" in n["mport"].replace("-", ":"):
            lines.append(f"    ip6tables -t nat -A PREROUTING -p udp --dport {mport_iptables} -j REDIRECT --to-ports {port}")
    return "\n".join(lines)


def diagnostics(n=None):
    lines = ["防火墙只读诊断；云安全组和外部 UDP 可达性需另行验证。"]
    commands = [["ss", "-lntup"], ["free", "-m"]]
    if shutil.which("ufw"):
        commands.append(["ufw", "status", "verbose"])
    if shutil.which("nft"):
        commands.append(["nft", "list", "ruleset"])
    if shutil.which("iptables"):
        commands.append(["iptables", "-w", "2", "-L", "-n", "-v"])
    if shutil.which("ip6tables"):
        commands.append(["ip6tables", "-w", "2", "-L", "-n", "-v"])
    for args in commands:
        try:
            lines.append(run(args, timeout=10))
        except PilotError as e:
            lines.append(str(e))
    if n:
        lines.append(firewall_reference(n))
    return "\n".join(lines)
