#!/usr/bin/env python3
"""Disposable standalone-service takeover tests. Never switch a pre-existing service."""
import argparse
import copy
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
from nodepilot_pkg.adoption import Adoptions, SourceService, digest
from nodepilot_pkg.certificates import prepare
from nodepilot_pkg.common import PilotError, atomic_bytes, atomic_json, private_dir, read_json, root_required, run
from nodepilot_pkg.config import generate
from nodepilot_pkg.cores import Cores, verify_elf
from nodepilot_pkg.manager import Manager, OWNER
from nodepilot_pkg.model import host, new_node
from nodepilot_pkg.services import Services
from nodepilot_pkg.share import share
from nodepilot_pkg.system import architecture


def free_port(kind=socket.SOCK_STREAM):
    with socket.socket(socket.AF_INET, kind) as sock:
        sock.bind(("0.0.0.0", 0))
        return sock.getsockname()[1]


def manager_at(state):
    cores = Cores(state / "cores")
    return Manager(state, cores, Services(state, Path("/run/systemd/system"), cores.root))


def original_unit(binary, config):
    core = binary.name
    args = f"run -config {config}" if core == "xray" else f"run -c {config}"
    return (f"# NodePilot disposable adoption SOURCE\n[Unit]\nDescription=Disposable original {core}\n"
            f"[Service]\nExecStart={binary} {args}\nUser=root\nUMask=0077\n"
            "Environment=GOMEMLIMIT=64MiB\nEnvironment=GOGC=50\nEnvironment=GOMAXPROCS=1\n"
            "[Install]\nWantedBy=multi-user.target\n")


def snapshot_sources(m, sources):
    reader = SourceService()
    return [reader.inspect(entry["service"]) for entry in sources]


def setup():
    state = Path("/var/lib") / ("nodepilot-adoption-test-" + secrets.token_hex(6))
    m = manager_at(state)
    m.initialize()
    original = Cores(ROOT / ".test-tools/cores")
    for core in ("xray", "sing-box"):
        source = original.binary(core)
        metadata = read_json(source.parent / "verified.json")
        destination = private_dir(state / "cores" / core / (metadata["version"] + "-" + secrets.token_hex(4)))
        shutil.copy2(source, destination / core)
        verify_elf(destination / core, architecture())
        assert digest(source) == digest(destination / core)
        shutil.copy2(source.parent / "verified.json", destination / "verified.json")
        m.cores.switch(core, destination)
        copy = private_dir(state / "original-binaries" / core) / core
        shutil.copy2(source, copy)
        assert digest(source) == digest(copy)
    address = host(os.environ["SSH_CONNECTION"].split()[2], public=True)
    private, public = m.cores.keypair()
    nodes = []
    for protocol in ("reality", "anytls", "hysteria2"):
        n = new_node(protocol, "TEST original " + protocol, address, free_port(socket.SOCK_DGRAM if protocol == "hysteria2" else socket.SOCK_STREAM),
                     "0.0.0.0", "www.bing.com" if protocol == "reality" else "tls.example.com")
        if protocol == "reality":
            n["reality"] = dict(mode="external", private_key=private, public_key=public, uuid=str(uuid.uuid4()),
                target="www.bing.com:443", server_names=[n["sni"]], short_id=secrets.token_hex(8), fingerprint="chrome")
        else:
            n["tls"] = dict(mode="selfsigned", certificate="/nonexistent", key="/nonexistent")
            prepare(n, private_dir(state / ("original-cert-" + protocol)))
            atomic_bytes(state / (protocol + "-certificate.pem"), Path(n["tls"]["certificate"]).read_bytes())
        nodes.append(n)
    sources = []
    try:
        for core in ("xray", "sing-box"):
            selected = [n for n in nodes if n["core"] == core]
            config = generate(selected[0])
            for node in selected[1:]:
                config["inbounds"].extend(generate(node)["inbounds"])
            path = state / ("original-" + core + ".json")
            atomic_json(path, config)
            m.cores.check(selected[0], path)
            service = "nodepilot-adoption-source-" + secrets.token_hex(8) + ".service"
            unit = m.services.units / service
            assert not run(["systemctl", "show", service, "-p", "FragmentPath", "--value"])
            binary = state / "original-binaries" / core / core
            text = original_unit(binary, path)
            atomic_bytes(unit, text.encode(), mode=0o644)
            sources.append({"service": service, "unit": str(unit), "content": text, "config": str(path), "config_sha256": digest(path)})
            run(["systemctl", "daemon-reload"])
            run(["systemctl", "enable", "--runtime", service])
            run(["systemctl", "start", service])
            assert run(["systemctl", "is-active", service]) == "active"
        atomic_json(state / "sources.json", {"sources": sources, "original_nodes": nodes})
        atomic_json(ROOT / ".test-tools/adoption-state.json", {"state": str(state)})
        # Failure occurs after native check, when the replacement listener starts.
        controller = Adoptions(m)
        with m.locked():
            failed = controller.prepare(sources[0]["service"], address)
            blocked = socket.socket()
            native = m.cores.check
            restore = controller.source.restore
            def occupy(node, path, binary=None):
                native(node, path, binary)
                blocked.bind(("0.0.0.0", node["port"]))
                blocked.listen()
            def release_then_restore(snapshot):
                blocked.close()
                return restore(snapshot)
            try:
                with patch.object(m.cores, "check", side_effect=occupy), patch.object(controller.source, "restore", side_effect=release_then_restore):
                    try:
                        controller.execute(failed["id"], confirmed=True)
                    except PilotError:
                        pass
                    else:
                        raise AssertionError("expected replacement failure")
                assert not m.nodes()
                assert controller.load(failed["id"])["phase"] == "rolled_back"
                assert run(["systemctl", "is-active", sources[0]["service"]]) == "active"
            finally:
                blocked.close()
            print("real replacement restart failure restored original service/state OK", flush=True)
            interrupted = controller.prepare(sources[0]["service"], address)
            interrupted["authorized"] = True
            controller.save(interrupted, "migrating")
            controller.source.stop(interrupted["source"])
            m.apply(interrupted["nodes"][0], probe=False)
            controller.recover()
            assert not m.nodes()
            print("persistent interruption record recovery using real services OK", flush=True)
        payload_port = free_port()
        script = "import http.server,sys; data=b'A'*1048576\nclass H(http.server.BaseHTTPRequestHandler):\n def do_GET(self):\n  self.send_response(200);self.send_header('Content-Length',str(len(data)));self.end_headers();self.wfile.write(data)\n def log_message(self,*args): pass\nhttp.server.ThreadingHTTPServer(('127.0.0.1',int(sys.argv[1])),H).serve_forever()"
        log = (state / "payload.log").open("wb")
        process = subprocess.Popen([sys.executable, "-c", script, str(payload_port)], stdout=log, stderr=log, start_new_session=True)
        log.close()
        import hashlib
        atomic_json(state / "payload.json", {"port": payload_port, "bytes": 1048576, "sha256": hashlib.sha256(b"A" * 1048576).hexdigest()})
        atomic_json(state / "payload-process.json", {"pid": process.pid})
        export(state, nodes)
        print(json.dumps({"ready": True, "private_state": str(state), "original_services": 2, "protocols": 3}), flush=True)
    except BaseException:
        atomic_json(state / "sources.json", {"sources": sources, "original_nodes": nodes})
        cleanup(state)
        raise


def export(state, nodes):
    atomic_json(state / "clients.json", {"nodes": [{"protocol": n["protocol"], "uri": share(n)} for n in nodes]})


def operate(action, state):
    m, source = manager_at(state), read_json(state / "sources.json")
    controller = Adoptions(m)
    with m.locked():
        if action == "adopt":
            identities = []
            for entry in source["sources"]:
                record = controller.prepare(entry["service"], source["original_nodes"][0]["server"])
                controller.execute(record["id"], confirmed=True)
                identities.append(record["id"])
                assert digest(entry["config"]) == entry["config_sha256"]
                assert run(["systemctl", "show", entry["service"], "-p", "ActiveState", "--value"]) == "inactive"
            atomic_json(state / "active-adoptions.json", {"ids": identities})
            assert len(m.nodes()) == 3 and all(m.services.active(n) for n in m.nodes())
            export(state, m.nodes())
            print("both source services adopted; original files preserved; three new nodes active", flush=True)
        else:
            identities = read_json(state / "active-adoptions.json")["ids"]
            for identity in reversed(identities):
                controller.restore(identity, confirmed=True)
            assert not m.nodes()
            for entry in source["sources"]:
                assert digest(entry["config"]) == entry["config_sha256"]
                assert run(["systemctl", "is-active", entry["service"]]) == "active"
                assert run(["systemctl", "show", entry["service"], "-p", "UnitFileState", "--value"]) == "enabled-runtime"
            export(state, source["original_nodes"])
            print("original services returned active/enabled-runtime; original files/credentials preserved", flush=True)


def cleanup(state):
    state = Path(state)
    if state.resolve() != state or state.parent != Path("/var/lib") or not state.name.startswith("nodepilot-adoption-test-") or (state / ".owner").read_text() != OWNER:
        raise PilotError("拒绝越界测试清理。")
    m = manager_at(state)
    with m.locked():
        m.recover()
        Adoptions(m).recover()
        for node in m.nodes():
            m.delete(node["id"])
    for entry in read_json(state / "sources.json")["sources"]:
        path = Path(entry["unit"])
        assert path.parent == Path("/run/systemd/system") and path.name == entry["service"]
        if path.exists():
            assert path.read_text() == entry["content"]
            run(["systemctl", "disable", "--runtime", "--now", entry["service"]])
            path.unlink()
    run(["systemctl", "daemon-reload"])
    if (state / "payload-process.json").exists():
        pid = read_json(state / "payload-process.json")["pid"]
        command = Path(f"/proc/{pid}/cmdline")
        if command.exists() and b"ThreadingHTTPServer" in command.read_bytes():
            os.kill(pid, 15)
    shutil.rmtree(state)
    print("disposable original services, adopted nodes and private test state cleaned", flush=True)


def main():
    p = argparse.ArgumentParser()
    p.add_argument("action", choices=("setup", "adopt", "return", "cleanup"))
    args = p.parse_args()
    root_required()
    if args.action == "setup":
        setup()
    else:
        state = Path(read_json(ROOT / ".test-tools/adoption-state.json")["state"])
        cleanup(state) if args.action == "cleanup" else operate(args.action, state)


if __name__ == "__main__":
    main()
