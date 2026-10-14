#!/usr/bin/env python3
"""Explicit disposable VPS test. Private metadata stays in an owned /var/lib subtree."""
import argparse
import copy
import hashlib
import json
import os
import secrets
import shutil
import socket
import subprocess
import sys
import uuid
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from nodepilot_pkg.caddy import Caddy, UNIT
from nodepilot_pkg.certificates import prepare
from nodepilot_pkg.common import PilotError, atomic_json, private_dir, read_json, root_required, run
from nodepilot_pkg.cores import Cores, verify_elf
from nodepilot_pkg.manager import Manager, OWNER
from nodepilot_pkg.model import host, new_node
from nodepilot_pkg.services import Services
from nodepilot_pkg.share import share
from nodepilot_pkg.system import architecture


def free_port():
    with socket.socket() as s:
        s.bind(("0.0.0.0", 0))
        return s.getsockname()[1]


def manager_at(state):
    cores = Cores(state / "cores")
    services = Services(state, Path("/run/systemd/system"), cores.root)
    return Manager(state, cores, services, Caddy(state, cores, services.units))


def setup():
    if run(["systemctl", "show", UNIT, "-p", "FragmentPath", "--value"]):
        raise PilotError("已有同名 Caddy 单元，拒绝运行临时测试。")
    state = Path("/var/lib") / ("nodepilot-caddy-test-" + secrets.token_hex(6))
    m = manager_at(state)
    m.initialize()
    source = Cores(ROOT / ".test-tools/cores")
    # ProtectHome=true and /run may be noexec: use a private /var/lib subtree.
    for name in ("xray", "sing-box", "caddy"):
        original = source.binary(name)
        version = read_json(original.parent / "verified.json")["version"]
        destination = private_dir(state / "cores" / name / (version + "-" + secrets.token_hex(4)))
        shutil.copy2(original, destination / name)
        assert hashlib.sha256(original.read_bytes()).digest() == hashlib.sha256((destination / name).read_bytes()).digest()
        verify_elf(destination / name, architecture())
        shutil.copy2(original.parent / "verified.json", destination / "verified.json")
        m.cores.switch(name, destination)
    address = host(os.environ.get("SSH_CONNECTION", "").split()[2], public=True)
    private, public = m.cores.keypair()
    try:
        with m.locked():
            created = []
            for domain in ("one.example.com", "two.example.com"):
                n = new_node("reality", "TEST self target", address, free_port(), "0.0.0.0", domain)
                local = free_port()
                while local == n["port"]:
                    local = free_port()
                n["reality"] = dict(mode="self", private_key=private, public_key=public, uuid=str(uuid.uuid4()),
                    target=f"127.0.0.1:{local}", server_names=[domain], short_id=secrets.token_hex(8), fingerprint="chrome")
                certificate = private_dir(state / ("test-cert-" + n["id"]))
                holder = {"sni": domain, "tls": dict(mode="selfsigned", certificate="/nonexistent", key="/nonexistent")}
                prepare(holder, certificate)
                n["caddy"] = dict(domain=domain, port=local, certificate_mode="import", upstream="",
                    certificate=holder["tls"]["certificate"], key=holder["tls"]["key"])
                created.append(m.apply(n))
            # Real core passes validation, then another socket grabs its new port.
            a, b = created
            blocked = socket.socket()
            changed = copy.deepcopy(a)
            changed["port"] = free_port()
            native = m.cores.check
            def occupy(n, path, binary=None):
                native(n, path, binary)
                blocked.bind(("0.0.0.0", changed["port"]))
                blocked.listen()
            try:
                with patch.object(m.cores, "check", side_effect=occupy):
                    try:
                        m.apply(changed)
                    except PilotError:
                        pass
                    else:
                        raise AssertionError("expected restart failure")
            finally:
                blocked.close()
            assert m.get(a["id"]) == a and m.services.active(b)
            m.caddy.ready(a, timeout=5)
            m.caddy.ready(b, timeout=5)
            print("real Xray restart failure: Caddy/metadata rollback OK", flush=True)
            # Delete one node, verify the surviving local target, then restore both.
            backup = m.backup()
            m.delete(a["id"])
            assert m.services.active(b)
            m.caddy.ready(b, timeout=5)
            m.delete(b["id"])
            assert not (m.services.units / UNIT).exists()
            m.restore(backup)
            for n in m.nodes():
                m.caddy.ready(n, timeout=5)
            print("two local targets / isolated deletion / certificate backup+restore OK", flush=True)
            # Persist an additional successful listen/SNI/certificate edit.
            n = m.get(a["id"])
            n["port"] = free_port()
            n["sni"] = n["caddy"]["domain"] = "edited.example.com"
            n["reality"]["server_names"] = [n["sni"]]
            holder = {"sni": n["sni"], "tls": dict(mode="selfsigned", certificate="/nonexistent", key="/nonexistent")}
            prepare(holder, private_dir(state / "edited-cert"))
            n["caddy"].update(certificate=holder["tls"]["certificate"], key=holder["tls"]["key"])
            m.apply(n)
            print("SNI/port/certificate edit native+TLS checks OK", flush=True)
        # Payload contains no secrets. HTTP is loopback-only and reached via proxy.
        port = free_port()
        script = "import http.server,sys; data=b'N'*1048576\nclass H(http.server.BaseHTTPRequestHandler):\n def do_GET(self):\n  self.send_response(200);self.send_header('Content-Length',str(len(data)));self.end_headers();self.wfile.write(data)\n def log_message(self,*args): pass\nhttp.server.ThreadingHTTPServer(('127.0.0.1',int(sys.argv[1])),H).serve_forever()"
        log = (state / "payload.log").open("wb")
        process = subprocess.Popen([sys.executable, "-c", script, str(port)], stdout=log, stderr=log, start_new_session=True)
        log.close()
        atomic_json(state / "payload.json", {"port": port, "bytes": 1048576, "sha256": hashlib.sha256(b"N" * 1048576).hexdigest()})
        atomic_json(state / "payload-process.json", {"pid": process.pid})
        atomic_json(state / "clients.json", {"nodes": [{"protocol": n["protocol"], "uri": share(n)} for n in m.nodes()]})
        atomic_json(ROOT / ".test-tools/caddy-state.json", {"state": str(state)})
        print(json.dumps({"ready": True, "private_state": str(state), "nodes": len(m.nodes())}), flush=True)
    except BaseException:
        cleanup(state)
        raise


def cleanup(state):
    state = Path(state)
    if state.parent != Path("/var/lib") or not state.name.startswith("nodepilot-caddy-test-") or state.is_symlink():
        raise PilotError("拒绝越界测试清理。")
    if not (state / ".owner").is_file() or (state / ".owner").read_text() != OWNER:
        raise PilotError("缺少临时测试所有权标记。")
    m = manager_at(state)
    with m.locked():
        m.recover()
        for n in m.nodes():
            m.delete(n["id"])
    process_record = state / "payload-process.json"
    if process_record.exists():
        pid = read_json(process_record)["pid"]
        command = Path(f"/proc/{pid}/cmdline")
        if command.exists() and b"ThreadingHTTPServer" in command.read_bytes():
            os.kill(pid, 15)
    # All paths are a random-ID subtree created exclusively by this test.
    shutil.rmtree(state)
    print("temporary self-target nodes / Caddy unit / payload cleaned", flush=True)


def main():
    p = argparse.ArgumentParser()
    p.add_argument("action", choices=("setup", "cleanup"))
    args = p.parse_args()
    root_required()
    if args.action == "setup":
        setup()
    else:
        cleanup(read_json(ROOT / ".test-tools/caddy-state.json")["state"])


if __name__ == "__main__":
    main()
