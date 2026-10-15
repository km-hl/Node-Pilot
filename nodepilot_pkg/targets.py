"""VPS-local, bounded Reality target screening. No ping ranking or fixed winner."""
import ipaddress
import json
import socket
import ssl
import statistics
import subprocess
import re
import selectors
import os
import sys
import time
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

from .common import PilotError, run
from .model import host, split_target

# Candidates only: none is promised to be suitable or CDN-free. Screen at runtime.
CANDIDATES = ("www.amazon.com", "aws.amazon.com", "www.samsung.com", "www.nvidia.com",
              "www.amd.com", "www.intel.com", "www.sony.com", "www.oracle.com")


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


class TargetFailure(PilotError):
    def __init__(self, code, message):
        self.code = code
        super().__init__(message)


def run_openssl(args, timeout):
    """Stop the child as soon as output exceeds 64KiB, before buffering more."""
    process=subprocess.Popen(args,stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.STDOUT)
    process.stdin.close()
    data=bytearray();deadline=time.monotonic()+timeout
    try:
        with selectors.DefaultSelector() as selector:
            selector.register(process.stdout,selectors.EVENT_READ)
            while True:
                remaining=deadline-time.monotonic()
                if remaining<=0:raise subprocess.TimeoutExpired(args,timeout)
                if not selector.select(min(remaining,.2)):continue
                block=os.read(process.stdout.fileno(),min(4096,65537-len(data)))
                if not block:break
                data.extend(block)
                if len(data)>65536:raise TargetFailure('unverified','OpenSSL 输出超出检查上限')
        code=process.wait(timeout=max(.01,deadline-time.monotonic()))
        return subprocess.CompletedProcess(args,code,bytes(data),b'')
    finally:
        if process.poll() is None:
            process.kill();process.wait()
        process.stdout.close()


def tls_handshake(ip, port, sni, timeout=4):
    """Verify the actual negotiated group, ALPN and peer using system OpenSSL."""
    address = f"[{ip}]:{port}" if ":" in ip else f"{ip}:{port}"
    verify = "-verify_ip" if is_ip(sni) else "-verify_hostname"
    args = ["openssl", "s_client", "-connect", address, "-servername", sni, "-tls1_3",
            "-alpn", "h2", "-groups", "X25519", "-verify_return_error", verify, sni, "-no_ticket"]
    started = time.monotonic()
    try:
        result = run_openssl(args,timeout)
    except subprocess.TimeoutExpired as error:
        raise TargetFailure("timeout", "TCP/TLS 握手超时") from error
    except OSError as error:
        raise TargetFailure("unverified", "OpenSSL 不可用，X25519 未验证") from error
    output = (result.stdout + result.stderr).decode(errors="replace")
    if len(output) > 65536:
        raise TargetFailure("unverified", "OpenSSL 输出超出检查上限")
    if result.returncode:
        if re.search(r'Network is unreachable|No route to host',output,re.I):
            raise TargetFailure('network_unreachable','此地址/地址族无可用路由')
        if re.search(r'Connection refused|connect:errno',output,re.I):
            raise TargetFailure('connection','目标 TCP 连接失败')
        code = "certificate" if re.search(r"verify error|certificate verify|hostname mismatch", output, re.I) else "handshake"
        raise TargetFailure(code, "证书链/SNI 验证失败" if code == "certificate" else "TCP/TLS 握手失败（要求 TLS 1.3 与 X25519）")
    if not re.search(r"TLSv1\.3", output):
        raise TargetFailure("tls", "未协商 TLS 1.3")
    if not re.search(r"ALPN protocol:\s*h2\b", output):
        raise TargetFailure("alpn", "未协商 HTTP/2 h2")
    group = re.search(r"(?:Server|Peer) Temp Key:\s*(X25519)[,\s]", output)
    if not group:
        raise TargetFailure("unverified", "实际 X25519 密钥交换组未验证")
    if not re.search(r"Verify return code:\s*0\s*\(ok\)", output):
        raise TargetFailure("certificate", "无法证明证书链和 SNI 验证通过")
    return {"elapsed_ms": (time.monotonic() - started) * 1000, "group": group[1],
            "tls": "TLSv1.3", "alpn": "h2", "certificate_verified": True}


def is_ip(value):
    try:
        ipaddress.ip_address(value)
        return True
    except ValueError:
        return False


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
    results, failures = [], []
    families = [[ip for ip in ips if ipaddress.ip_address(ip).version == version] for version in (4, 6)]
    # Interleave families; do not let one unreachable first address hide the rest.
    tested = [family[i] for i in range(3) for family in families if len(family) > i]
    for ip in tested:
        samples = []
        try:
            for _ in range(3):
                sample = tls_handshake(ip, port, sni)
                samples.append(sample["elapsed_ms"])
            warnings, status = [], None
            try:
                status, headers = http_headers(ip, port, sni)
                if cf_headers(headers):
                    raise TargetFailure("cloudflare", "排除：HTTP 响应含 Cloudflare CDN 标记")
                if 300 <= status < 400 or "location" in headers:
                    warnings.append("根页面有跳转（未跟随），不影响已验证的 TLS 条件")
                elif not 200 <= status < 300:
                    warnings.append(f"根页面 HTTP {status}，网页质量需人工评估")
            except TargetFailure:
                raise
            except (PilotError, OSError, ssl.SSLError):
                warnings.append("HTTP/1.1 网页检查未成功；TLS/h2 已验证，CF 响应头未验证")
            results.append({"ip": ip, "family": ipaddress.ip_address(ip).version,
                            "median_ms": round(statistics.median(samples), 2),
                            "spread_ms": round(max(samples) - min(samples), 2), "samples": 3,
                            "group": sample["group"], "http_status": status, "warnings": warnings})
        except (PilotError, OSError) as error:
            if getattr(error, "code", "") == "cloudflare":
                raise
            failures.append({"ip": ip, "code": getattr(error, "code", "network"), "reason": str(error)})
            continue
    if not results:
        raise PilotError("目标全部已测地址失败/未验证：" + "; ".join(f"{f['ip']} {f['code']} {f['reason']}" for f in failures))
    best = min(results, key=lambda x: (x["median_ms"], x["spread_ms"]))
    pinned = f"[{best['ip']}]:{port}" if best["family"] == 6 else f"{best['ip']}:{port}"
    return {"target": pinned, "requested_target": target, "sni": sni, "median_ms": best["median_ms"], "spread_ms": best["spread_ms"],
            "feasible": True, "tls": "TLSv1.3", "alpn": "h2", "group": best["group"],
            "certificate_verified": True, "addresses": results, "failures": failures,
            "untested_addresses": [ip for ip in ips if ip not in tested], "warnings": best["warnings"],
            "checked_at": datetime.now(timezone.utc).isoformat(), "cf_screened": True,
            "note": "VPS 至目标三次 OpenSSL TCP+TLS（含子进程开销）中位时延；不是长期或客户端联通证明。"}


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
            message = f"{domain}：TLS1.3/h2/X25519/证书通过；{result['median_ms']} ms，波动 {result['spread_ms']} ms"
            message += "；" + "；".join(result.get("warnings", [])) if result.get("warnings") else ""
            for failure in result.get("failures", []):
                message += f"\n  {failure['ip']}：{failure['code']} {failure['reason']}"
        except PilotError as e:
            message = f"{domain}：{e}"
        if report:
            report(message)
    return sorted(ranked, key=lambda x: (x["median_ms"], x["spread_ms"]))


def target_identity(node):
    import hashlib
    import json
    return hashlib.sha256(json.dumps([node['sni'], node['reality']['target'],
                                     node['reality']['server_names']],sort_keys=True).encode()).hexdigest()


def recheck_target(state, node):
    """Manual read-only network recheck; never select a replacement address."""
    from .common import atomic_json, private_dir
    if node['protocol']!='reality' or node['reality'].get('mode')=='self':
        raise PilotError('此操作只适用于第三方 Reality 目标；自建目标请查看 Caddy 证书/服务。')
    fixed, _ = split_target(node['reality']['target'])
    record = {'identity':target_identity(node), 'checked_at':datetime.now(timezone.utc).isoformat(),
              'status':'检测失败，需要手动重选', 'dns':[], 'dns_drift':None, 'target':node['reality']['target']}
    try:
        addresses, _ = resolve(node['sni'])
        record.update(dns=addresses, dns_drift=fixed not in addresses)
    except (PilotError, OSError):
        record['dns_error']='当前 DNS 未验证'
    try:
        # inspect_target probes precisely this fixed IP, rather than a new DNS IP.
        if not is_ip(fixed):
            raise PilotError('旧目标尚未固定 IP；请在菜单 5 手动重新检测并选择目标。')
        for sni in node['reality']['server_names']:
            check = inspect_target(node['reality']['target'],sni,cloudflare_networks())
        record.update(status='最近检测通过：TLS1.3 / X25519 / h2 / 可信证书', result=check)
    except (PilotError, OSError) as error:
        record['diagnostic']=str(error)
    if record['dns_drift']:
        record['status'] += '；固定 IP 与当前 DNS 存在差异，请评估手动重选'
    atomic_json(private_dir(Path(state)/'target-status')/(node['id']+'.json'),record)
    return record


def target_status(state, node):
    from .common import read_json
    path = Path(state)/'target-status'/(node['id']+'.json')
    if path.exists():
        record = read_json(path)
        if record.get('identity')==target_identity(node):
            return record['status']+'；检测时间 '+record['checked_at']
    selection = node['reality'].get('selection',{})
    if selection.get('target') == node['reality']['target']:
        return '选站时检测通过；时间 '+selection.get('checked_at','未知')+'；菜单 8 → 5 可手动复查固定 IP/DNS'
    return '尚未检测当前固定目标；菜单 8 → 5 可检查并评估手动重选'
