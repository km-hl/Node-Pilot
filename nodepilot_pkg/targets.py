"""VPS-local, bounded Reality target screening. No ping ranking or fixed winner."""
import ipaddress
import json
import socket
import ssl
import statistics
import sys
import time
import urllib.request
from datetime import datetime, timezone

from .common import PilotError, run
from .model import host, split_target

# Candidates only: none is promised to be suitable or CDN-free. Screen at runtime.
CANDIDATES = ("www.apple.com", "www.microsoft.com", "www.amazon.com", "www.samsung.com",
              "www.oracle.com", "www.bing.com", "www.google.com", "dl.google.com")


def cloudflare_networks():
    # The official API provides the same public ranges and avoids a dependency on
    # the www site's access policy. Fall back to the official text endpoints.
    api = "https://api.cloudflare.com/client/v4/ips"
    try:
        with urllib.request.urlopen(urllib.request.Request(api, headers={"User-Agent": "NodePilot"}), timeout=5) as response:
            if response.geturl() != api:
                raise ValueError("unexpected redirect")
            data = response.read(16385)
        if len(data) > 16384:
            raise ValueError("oversized")
        record = json.loads(data)
        if not record.get("success"):
            raise ValueError("unsuccessful")
        groups = [record["result"][f"ipv{v}_cidrs"] for v in (4, 6)]
        ranges = [ipaddress.ip_network(value) for group in groups for value in group]
        if not all(groups) or len(ranges) > 256 or any(not n.is_global for n in ranges):
            raise ValueError("invalid ranges")
        return ranges
    except (OSError, ValueError, KeyError, TypeError):
        pass
    networks = []
    for family in (4, 6):
        url = f"https://www.cloudflare.com/ips-v{family}"
        try:
            with urllib.request.urlopen(url, timeout=5) as response:
                if response.geturl() != url:
                    raise ValueError("unexpected source redirect")
                content = response.read(16385)
            if len(content) > 16384:
                raise ValueError("oversized")
            ranges = [ipaddress.ip_network(line) for line in content.decode("ascii").splitlines() if line.strip()]
            if not ranges or len(ranges) > 256 or any(n.version != family or not n.is_global for n in ranges):
                raise ValueError("invalid networks")
            networks.extend(ranges)
        except (OSError, ValueError, UnicodeError) as e:
            raise PilotError("无法取得 Cloudflare 官方 IP 段；暂停第三方选站，不把未知结果当作非 CF。") from e
    return networks


def resolve(domain):
    host(domain, public=True)
    # A subprocess bounds libc DNS lookup and leaves no background resolver/thread.
    script = "import socket,json,sys; print(json.dumps([(x[4][0],x[3]) for x in socket.getaddrinfo(sys.argv[1],None,0,socket.SOCK_STREAM,0,socket.AI_CANONNAME)]))"
    try:
        answers = json.loads(run([sys.executable, "-c", script, domain], timeout=5))
        ips = sorted({x[0] for x in answers})
        canonical = {x[1].lower().rstrip(".") for x in answers if x[1]}
        if not ips or len(ips) > 32 or any(not ipaddress.ip_address(ip).is_global for ip in ips):
            raise ValueError("non-public DNS")
        return ips, canonical
    except (ValueError, TypeError, IndexError) as e:
        raise PilotError("目标 DNS 为空、过多或包含非公网地址。") from e


def exclude_cf(ips, canonical, networks):
    if any(any(ipaddress.ip_address(ip) in network for network in networks) for ip in ips):
        raise PilotError("排除：DNS 地址位于 Cloudflare 官方 IP 段。")
    if any(name == "cloudflare.com" or name.endswith((".cloudflare.com", ".cloudflare.net", ".workers.dev", ".pages.dev")) for name in canonical):
        raise PilotError("排除：DNS 规范名称指向 Cloudflare。")


def cf_headers(headers):
    return "cf-ray" in headers or "cf-cache-status" in headers or "cloudflare" in headers.get("server", "").lower()


def http_headers(ip, port, sni, timeout=3):
    context = ssl.create_default_context()
    context.minimum_version = ssl.TLSVersion.TLSv1_3
    context.set_alpn_protocols(["http/1.1"])
    with socket.create_connection((ip, port), timeout=timeout) as sock:
        with context.wrap_socket(sock, server_hostname=sni) as tls:
            tls.sendall(f"GET / HTTP/1.1\r\nHost: {sni}\r\nUser-Agent: Mozilla/5.0\r\nConnection: close\r\n\r\n".encode("ascii"))
            data = bytearray()
            deadline = time.monotonic() + timeout
            while b"\r\n\r\n" not in data and len(data) <= 16384:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError()
                tls.settimeout(remaining)
                block = tls.recv(min(2048, 16385 - len(data)))
                if not block:
                    break
                data.extend(block)
            if b"\r\n\r\n" not in data or len(data) > 16384:
                raise PilotError("排除：无法取得有界 HTTP 响应头。")
            lines = bytes(data).split(b"\r\n\r\n", 1)[0].decode("iso-8859-1").split("\r\n")
            try:
                status = int(lines[0].split()[1])
            except (ValueError, IndexError) as e:
                raise PilotError("排除：HTTP 响应无效。") from e
            headers = {k.strip().lower(): v.strip() for line in lines[1:] if ":" in line for k, v in [line.split(":", 1)]}
            return status, headers


def inspect_target(target, sni, networks):
    h, port = split_target(target)
    host(sni, public=True)
    if not 1 <= port <= 65535:
        raise PilotError("第三方 Reality 目标端口为 1–65535。")
    ips, canonical = resolve(h)
    exclude_cf(ips, canonical, networks)
    if h != sni:
        sni_ips, names = resolve(sni)
        exclude_cf(sni_ips, names, networks)
    context = ssl.create_default_context()
    context.minimum_version = ssl.TLSVersion.TLSv1_3
    context.set_alpn_protocols(["h2"])
    results = []
    families = [[ip for ip in ips if ipaddress.ip_address(ip).version == version] for version in (4, 6)]
    tested = [family[0] for family in families if family]
    for ip in tested:
        samples = []
        try:
            for _ in range(3):
                start = time.monotonic()
                with socket.create_connection((ip, port), timeout=3) as sock:
                    with context.wrap_socket(sock, server_hostname=sni) as tls:
                        if tls.version() != "TLSv1.3" or tls.selected_alpn_protocol() != "h2":
                            raise PilotError("排除：不支持 TLS 1.3 / HTTP/2。")
                        samples.append((time.monotonic() - start) * 1000)
            status, headers = http_headers(ip, port, sni)
            # CF on any reachable tested address excludes the entire hostname.
            if cf_headers(headers):
                raise PilotError("排除：HTTP 响应含 Cloudflare CDN 标记。")
            if 300 <= status < 400 or "location" in headers:
                raise PilotError("排除：该域名跳转至其他页面/域名。")
            if not 200 <= status < 300:
                raise PilotError("排除：根页面不可正常访问。")
            results.append({"ip": ip, "median_ms": round(statistics.median(samples), 2),
                            "spread_ms": round(max(samples) - min(samples), 2)})
        except (OSError, ssl.SSLError):
            continue
    if not results:
        raise PilotError("目标未通过三次有效证书/TLS 1.3/h2 握手及网页检查。")
    best = min(results, key=lambda x: (x["median_ms"], x["spread_ms"]))
    return {"target": target, "sni": sni, "median_ms": best["median_ms"], "spread_ms": best["spread_ms"],
            "checked_at": datetime.now(timezone.utc).isoformat(), "cf_screened": True,
            "note": "VPS 至目标 TCP+TLS 中位时延；仅预检，仍需真实 Reality 客户端验证。"}


def rank_targets(domains=None, report=None):
    domains = list(dict.fromkeys(domains or CANDIDATES))
    if not 1 <= len(domains) <= 12:
        raise PilotError("候选域名数量为 1–12。")
    networks, ranked = cloudflare_networks(), []
    for domain in domains:
        host(domain, public=True)
        try:
            result = inspect_target(domain + ":443", domain, networks)
            ranked.append(result)
            message = f"{domain}：{result['median_ms']} ms（TCP+TLS），波动 {result['spread_ms']} ms"
        except PilotError as e:
            message = f"{domain}：{e}"
        if report:
            report(message)
    return sorted(ranked, key=lambda x: (x["median_ms"], x["spread_ms"]))
