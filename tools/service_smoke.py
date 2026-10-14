#!/usr/bin/env python3
"""Optional root Linux systemd smoke test using /run only and loopback listeners."""
import copy
import secrets
import shutil
import socket
import sys
import uuid
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from nodepilot_pkg.common import PilotError, root_required, run
from nodepilot_pkg.cores import Cores
from nodepilot_pkg.manager import Manager
from nodepilot_pkg.model import new_node
from nodepilot_pkg.services import Services


def free_port(kind=socket.SOCK_STREAM):
    with socket.socket(socket.AF_INET, kind) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def main():
    root_required()
    if not Path("/run/systemd/system").is_dir():
        raise SystemExit("需要 systemd")
    state = Path("/run") / ("nodepilot-test-" + secrets.token_hex(6))
    cores = Cores(ROOT / ".test-tools/cores")
    services = Services(state, Path("/run/systemd/system"), cores.root)
    manager = Manager(state, cores, services)
    private, public = cores.keypair()
    ports = {"reality": free_port(), "anytls": free_port()}
    # HY2 reuses Reality's number on UDP to prove protocol-specific collision handling.
    ports["hysteria2"] = ports["reality"]
    while ports["anytls"] == ports["reality"]:
        ports["anytls"] = free_port()
    created = []
    try:
        with manager.locked():
            for protocol in ("reality", "anytls", "hysteria2"):
                n = new_node(protocol, "systemd smoke", "8.8.8.8", ports[protocol], "127.0.0.1", "example.com")
                if protocol == "reality":
                    n["reality"] = dict(private_key=private, public_key=public, uuid=str(uuid.uuid4()),
                        target="example.com:443", server_names=[n["sni"]], short_id=secrets.token_hex(8), fingerprint="chrome")
                else:
                    n["tls"] = dict(mode="selfsigned", certificate="/nonexistent", key="/nonexistent")
                saved = manager.apply(n, probe=False)
                created.append(saved)
                print(protocol, "systemd active, port", saved["port"], saved["transport"])
            tcp = run(["ss", "-lnt"])
            udp = run(["ss", "-lnu"])
            assert f"127.0.0.1:{ports['reality']}" in tcp
            assert f"127.0.0.1:{ports['anytls']}" in tcp
            assert f"127.0.0.1:{ports['hysteria2']}" in udp
            old = created[0]
            edited = copy.deepcopy(old)
            edited["port"] = free_port()
            manager.apply(edited, probe=False)
            assert manager.get(old["id"])["port"] == edited["port"]
            previous = manager.revision(old["id"])
            native_check = cores.check
            blocked = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            changed = copy.deepcopy(edited)
            changed["port"] = free_port()

            def occupy_after_check(n, path, binary=None):
                native_check(n, path, binary)
                blocked.bind(("127.0.0.1", changed["port"]))
                blocked.listen()

            try:
                with patch.object(cores, "check", side_effect=occupy_after_check):
                    try:
                        manager.apply(changed, probe=False)
                    except PilotError:
                        pass
                    else:
                        raise AssertionError("预期服务端口抢占导致启动失败")
            finally:
                blocked.close()
            assert manager.revision(old["id"]) == previous
            assert manager.get(old["id"]) == edited
            assert services.active(edited)
            print("real restart failure rollback OK")
            rss_total = 0
            for n in manager.nodes():
                pid = int(run(["systemctl", "show", n["service"], "-p", "MainPID", "--value"]))
                status = Path(f"/proc/{pid}/status").read_text()
                rss = int(next(x for x in status.splitlines() if x.startswith("VmRSS:")).split()[1])
                rss_total += rss
                print(n["protocol"], "idle RSS KiB", rss)
            print("aggregate idle RSS KiB", rss_total, "(WSL, not a 256MiB VPS benchmark)")
            manager.delete(created[1]["id"])
            assert services.active(manager.get(created[0]["id"]))
            assert services.active(manager.get(created[2]["id"]))
            print("single-node deletion isolation OK")
    finally:
        if state.exists():
            with manager.locked():
                manager.recover()
                for n in manager.nodes():
                    manager.delete(n["id"])
            for n in created:
                assert not (services.units / n["service"]).exists()
            shutil.rmtree(state)
    print("temporary test nodes and units cleaned")


if __name__ == "__main__":
    main()
