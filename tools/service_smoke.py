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
from nodepilot_pkg.common import PilotError, atomic_json, root_required, run
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
        # Binding must leave real running node PIDs, configs and links intact.
        from nodepilot_pkg.manager import OWNER
        from nodepilot_pkg.project_update import bind_repository
        from nodepilot_pkg.share import share
        program=state/'program';program.mkdir();(program/'.owner').write_text(OWNER)
        atomic_json(program/'source.json',{'schema':1,'provenance':'local-source'})
        def snapshot():
            return {n['id']:(run(['systemctl','show',n['service'],'-p','MainPID','--value']),
                (manager.revision(n['id'])/'config.json').read_bytes(),share(n)) for n in manager.nodes()}
        before=snapshot();source_before=(program/'source.json').read_bytes()
        record={'tag_name':'v0.4.0','assets':[{'name':name,'digest':'sha256:'+'a'*64,
            'browser_download_url':'https://github.com/example/project/releases/download/v0.4.0/'+name}
            for name in ('install.sh','nodepilot-0.4.0.tar.gz')]}
        pending=state/'core-pending.json';atomic_json(pending,{'schema':2})
        with patch('nodepilot_pkg.project_update.official_release',return_value=record) as network:
            try:bind_repository('example/project',True,program,manager)
            except PilotError:pass
            else:raise AssertionError('active transaction was not refused')
            network.assert_not_called()
            assert snapshot()==before and (program/'source.json').read_bytes()==source_before
            pending.unlink()  # Test-created inert fixture, never a real core upgrade.
            bind_repository('example/project',True,program,manager)
        assert snapshot()==before
        assert all(services.runtime(n)['healthy'] for n in manager.nodes())
        print('bind-source refusal/success preserves real node PIDs/configs/URIs/listeners OK (Release API fixture)')
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
