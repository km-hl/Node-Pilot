#!/usr/bin/env python3
"""Exercise exported URIs using a locally verified sing-box, with trusted test PEMs."""
import argparse
import concurrent.futures
import hashlib
import ipaddress
import json
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlsplit

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from nodepilot_pkg.common import atomic_bytes, atomic_json, read_json, run
from nodepilot_pkg.cores import Cores


def outbound(node, certificate=None):
    uri = urlsplit(node["uri"])
    query = {k: v[0] for k, v in parse_qs(uri.query).items()}
    if query.get("insecure", "0") != "0":
        raise ValueError("probe requires normal certificate verification")
    result = dict(type="vless" if uri.scheme == "vless" else uri.scheme, tag="proxy",
                  server=uri.hostname, server_port=uri.port,
                  tls={"enabled": True, "server_name": query["sni"]})
    if uri.scheme == "vless":
        result.update(uuid=unquote(uri.username), flow=query["flow"])
        result["tls"].update(utls={"enabled": True, "fingerprint": query["fp"]},
                             reality={"enabled": True, "public_key": query["pbk"], "short_id": query["sid"]})
    else:
        result["password"] = unquote(uri.username)
        result["tls"]["certificate_path"] = str(certificate)
        if uri.scheme == "hysteria2" and "obfs" in query:
            result["obfs"] = {"type": query["obfs"], "password": query["obfs-password"]}
    return result


def xray_client(node, port):
    uri = urlsplit(node["uri"])
    q = {k: v[0] for k, v in parse_qs(uri.query).items()}
    return {"log": {"loglevel": "warning"}, "inbounds": [{"listen": "127.0.0.1", "port": port,
        "protocol": "http", "settings": {}}], "outbounds": [{"protocol": "vless",
        "settings": {"vnext": [{"address": uri.hostname, "port": uri.port,
            "users": [{"id": unquote(uri.username), "encryption": "none", "flow": q["flow"]}]}]},
        "streamSettings": {"network": "tcp", "security": "reality", "realitySettings": {
            "serverName": q["sni"], "fingerprint": q["fp"], "password": q["pbk"], "shortId": q["sid"]}}}]}


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--private-dir", type=Path, required=True)
    p.add_argument("--requests", type=int, default=1)
    p.add_argument("--parallel", type=int, default=1)
    p.add_argument("--reality-core", choices=("sing-box", "xray"), default="sing-box")
    p.add_argument("--protocol", choices=("reality", "anytls", "hysteria2"))
    p.add_argument("--https-check", action="store_true", help="额外验证服务端域名解析、外网出站与端到端 HTTPS")
    args = p.parse_args()
    if not 1 <= args.requests <= 30 or not 1 <= args.parallel <= 4:
        p.error("bounded probe: 1..30 requests and 1..4 parallel")
    record = read_json(args.private_dir / "clients.json")
    payload = read_json(args.private_dir / "payload.json")
    cores = Cores(ROOT / ".test-tools/cores")
    for n in record["nodes"]:
        if args.protocol and args.protocol != n["protocol"]:
            continue
        core = args.reality_core if n["protocol"] == "reality" else "sing-box"
        binary = cores.binary(core)
        with tempfile.TemporaryDirectory(prefix="nodepilot-client-") as directory:
            tmp = Path(directory)
            with socket.socket() as sock:
                sock.bind(("127.0.0.1", 0))
                port = sock.getsockname()[1]
            cert = args.private_dir / (n["protocol"] + "-certificate.pem")
            config = {"log": {"level": "warn"}, "inbounds": [{"type": "mixed", "listen": "127.0.0.1", "listen_port": port}],
                      "outbounds": [outbound(n, cert)], "route": {"final": "proxy"}}
            if core == "xray":
                config = xray_client(n, port)
            atomic_json(tmp / "client.json", config)
            check = [binary, "run", "-test", "-config", tmp / "client.json"] if core == "xray" else [binary, "check", "-c", tmp / "client.json"]
            start = [str(binary), "run", "-config" if core == "xray" else "-c", str(tmp / "client.json")]
            run(check)
            with (tmp / "log").open("wb") as log:
                process = subprocess.Popen(start, stdout=log, stderr=log)
                try:
                    for _ in range(30):
                        try:
                            with socket.create_connection(("127.0.0.1", port), timeout=0.2):
                                break
                        except OSError:
                            time.sleep(0.1)
                    # Connect directly to the HTTP proxy. urllib bypasses loopback in
                    # proxy autodetection; an explicit absolute-form request avoids that.
                    import http.client
                    def transfer(index):
                        c = http.client.HTTPConnection("127.0.0.1", port, timeout=25)
                        try:
                            c.request("GET", f"http://127.0.0.1:{payload['port']}/payload")
                            response = c.getresponse()
                            data = response.read(payload["bytes"] + 1)
                            if response.status != 200 or len(data) != payload["bytes"] or hashlib.sha256(data).hexdigest() != payload["sha256"]:
                                print(json.dumps({"protocol": n["protocol"], "http_status": response.status,
                                    "received_bytes": len(data), "integrity_ok": False}), flush=True)
                                raise AssertionError("response integrity mismatch")
                        finally:
                            c.close()
                    started = time.monotonic()
                    with concurrent.futures.ThreadPoolExecutor(max_workers=args.parallel) as executor:
                        list(executor.map(transfer, range(args.requests)))
                    print(json.dumps({"protocol": n["protocol"], "external_encrypted_transfer": True,
                        "client_core": core,
                        "requests": args.requests, "bytes": payload["bytes"] * args.requests,
                        "elapsed_s": round(time.monotonic() - started, 3), "tls_verification": "normal"}), flush=True)
                    if args.https_check:
                        import ssl
                        connection = http.client.HTTPSConnection("127.0.0.1", port, timeout=25,
                                                                context=ssl.create_default_context())
                        try:
                            connection.set_tunnel("example.com", 443)
                            connection.request("GET", "/", headers={"Host": "example.com"})
                            response = connection.getresponse()
                            data = response.read(16384)
                            if response.status != 200 or not data:
                                raise AssertionError("internet HTTPS response failed")
                            print(json.dumps({"protocol": n["protocol"], "internet_https": True,
                                "server_dns": True, "inner_tls_verification": "normal"}), flush=True)
                        finally:
                            connection.close()
                finally:
                    process.terminate()
                    try:
                        process.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.wait()
                    log.flush()
                    atomic_bytes(args.private_dir / (n["protocol"] + "-client.log"), (tmp / "log").read_bytes()[-16384:])


if __name__ == "__main__":
    main()
