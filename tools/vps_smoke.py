#!/usr/bin/env python3
"""Explicit disposable VPS tests. Run only on an operator-authorized Linux host."""
import argparse
import copy
import hashlib
import json
import os
import secrets
import socket
import sys
import threading
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from nodepilot_pkg.common import PilotError, atomic_bytes, atomic_json, private_dir, read_json, root_required, run
from nodepilot_pkg.manager import Manager
from nodepilot_pkg.model import new_node
from nodepilot_pkg.share import share
from nodepilot_pkg.system import port_free, probe_reality


def available(preferred, transport="tcp"):
    try:
        port_free("0.0.0.0", preferred, transport)
        return preferred
    except PilotError:
        kind = socket.SOCK_DGRAM if transport == "udp" else socket.SOCK_STREAM
        with socket.socket(socket.AF_INET, kind) as sock:
            sock.bind(("0.0.0.0", 0))
            return sock.getsockname()[1]


def setup(directory):
    manager = Manager()
    root_required()
    directory = private_dir(directory)
    record = directory / "test-nodes.json"
    if record.exists():
        raise PilotError("测试清单已存在，拒绝重新创建。")
    server = os.environ.get("SSH_CONNECTION", "").split()
    if len(server) != 4:
        raise PilotError("请通过已授权的 SSH 会话运行，使用 SSH_CONNECTION 获取分享地址。")
    target = None
    # This is a disposable test pool, not a production default. A TLS preflight
    # alone does not prove REALITY interoperability; client_probe is required.
    for candidate in ("www.cloudflare.com", "www.apple.com", "www.microsoft.com"):
        try:
            probe_reality(candidate + ":443", candidate)
            target = candidate
            break
        except PilotError:
            continue
    if target is None:
        raise PilotError("未找到可通过 TLS 1.3 / h2 检查的测试 Reality 目标。")
    ports = {"reality": available(9443), "anytls": available(8443)}
    ports["hysteria2"] = available(ports["reality"], "udp")
    with manager.locked():
        for core in ("xray", "sing-box"):
            manager.update_core(core)
        nodes = []
        for protocol in ("reality", "anytls", "hysteria2"):
            sni = target if protocol == "reality" else "nodepilot-test.example.com"
            n = new_node(protocol, "NodePilot disposable test " + protocol, server[2], ports[protocol], sni=sni)
            if protocol == "reality":
                private, public = manager.cores.keypair()
                n["reality"] = dict(private_key=private, public_key=public, uuid=str(uuid.uuid4()),
                    target=target + ":443", server_names=[target], short_id=secrets.token_hex(8), fingerprint="chrome")
            else:
                n["tls"] = dict(mode="selfsigned", certificate="/nonexistent", key="/nonexistent")
                if protocol == "hysteria2":
                    n["obfs"] = {"type": "salamander", "password": secrets.token_urlsafe(32)}
            saved = manager.apply(n)
            nodes.append(saved)
            atomic_json(record, {"schema": 1, "nodes": nodes})
            print(json.dumps({"protocol": protocol, "port": saved["port"], "transport": saved["transport"], "active": True}), flush=True)
    print("setup complete; private test metadata is not printed", flush=True)


def lifecycle(directory):
    manager = Manager()
    record = read_json(directory / "test-nodes.json")
    with manager.locked():
        for original in record["nodes"]:
            n = manager.get(original["id"])
            n["port"] = available(n["port"] + 10, n["transport"])
            if n["protocol"] == "reality":
                n["reality"]["short_id"] = secrets.token_hex(8)
            else:
                n["password"] = secrets.token_urlsafe(32)
            manager.apply(n)
            # Restore parameters so the client manifest remains valid.
            manager.apply(original)
        print("all protocol port/credential edits and restoration OK", flush=True)
        old = manager.get(record["nodes"][0]["id"])
        changed = copy.deepcopy(old)
        changed["port"] = available(old["port"] + 20)
        previous = manager.revision(old["id"])
        native = manager.cores.check
        blocker = socket.socket(socket.AF_INET, socket.SOCK_STREAM)

        def occupy(n, path, binary=None):
            native(n, path, binary)
            blocker.bind(("0.0.0.0", changed["port"]))
            blocker.listen()

        try:
            with patch.object(manager.cores, "check", side_effect=occupy):
                try:
                    manager.apply(changed)
                except PilotError:
                    pass
                else:
                    raise AssertionError("服务启动预期失败却成功")
        finally:
            blocker.close()
        assert manager.get(old["id"]) == old
        assert manager.revision(old["id"]) == previous
        assert manager.services.active(old)
        print("real restart rollback OK", flush=True)
        # Real backup restoration: remove only our three recorded test nodes.
        backup = manager.backup()
        original_ids = {n["id"] for n in record["nodes"]}
        if {n["id"] for n in manager.nodes()} == original_ids:
            for n in record["nodes"]:
                manager.delete(n["id"])
            manager.restore(backup)
            # Restore marks TLS certs as import. Refresh paths before client export.
            record["nodes"] = [manager.get(n["id"]) for n in record["nodes"]]
            atomic_json(directory / "test-nodes.json", record)
            print("real backup restore to empty node set OK", flush=True)


def status(directory):
    manager = Manager()
    record = read_json(directory / "test-nodes.json")
    total = 0
    for n in record["nodes"]:
        current = manager.get(n["id"])
        pid = int(run(["systemctl", "show", current["service"], "-p", "MainPID", "--value"]))
        text = Path(f"/proc/{pid}/status").read_text()
        rss = int(next(x for x in text.splitlines() if x.startswith("VmRSS:")).split()[1])
        total += rss
        print(json.dumps({"protocol": n["protocol"], "active": manager.services.active(current), "rss_kib": rss}), flush=True)
    print(json.dumps({"total_rss_kib": total}), flush=True)


def export(directory):
    manager = Manager()
    record = read_json(directory / "test-nodes.json")
    clients = []
    for original in record["nodes"]:
        n = manager.get(original["id"])
        clients.append({"protocol": n["protocol"], "uri": share(n)})
        if n["protocol"] != "reality":
            atomic_bytes(directory / (n["protocol"] + "-certificate.pem"), Path(n["tls"]["certificate"]).read_bytes())
    # Only sharing credentials and public test certificates leave the server;
    # Reality private keys and TLS private keys never enter client artifacts.
    atomic_json(directory / "clients.json", {"schema": 1, "nodes": clients})
    print("private client URIs and public test PEMs exported; no server private keys", flush=True)


def payload_server(directory):
    block = bytes(range(256)) * 256
    digest = hashlib.sha256(block * 16).hexdigest()
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.send_header("Content-Length", str(len(block) * 16))
            self.end_headers()
            for _ in range(16):
                self.wfile.write(block)
        def log_message(self, *args):
            pass
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    atomic_json(directory / "payload.json", {"port": server.server_port, "sha256": digest, "bytes": 1048576})
    print("private loopback payload server ready", flush=True)
    server.serve_forever()


def cleanup(directory):
    manager = Manager()
    record = read_json(directory / "test-nodes.json")
    with manager.locked():
        for original in record["nodes"]:
            node_id = original["id"]
            if any(n["id"] == node_id for n in manager.nodes()):
                manager.delete(node_id)
                remaining = [n for n in manager.nodes() if n["id"] in {x["id"] for x in record["nodes"]}]
                assert all(manager.services.active(n) for n in remaining)
    print("test nodes removed with remaining-node isolation checks; manager and private backups retained", flush=True)


def main():
    p = argparse.ArgumentParser()
    p.add_argument("action", choices=("setup", "lifecycle", "status", "payload", "export", "cleanup"))
    p.add_argument("--private-dir", type=Path, required=True)
    args = p.parse_args()
    root_required()
    {"setup": setup, "lifecycle": lifecycle, "status": status, "payload": payload_server, "export": export, "cleanup": cleanup}[args.action](args.private_dir)


if __name__ == "__main__":
    main()
