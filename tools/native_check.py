#!/usr/bin/env python3
"""Download verified test cores and check configs. No production installation."""
import argparse
import copy
import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from nodepilot_pkg.certificates import prepare
from nodepilot_pkg.common import atomic_json, private_dir
from nodepilot_pkg.config import generate
from nodepilot_pkg.caddy import configuration
from nodepilot_pkg.cores import Cores
from nodepilot_pkg.model import new_node


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--download", action="store_true", help="下载校验官方内核，仅存入 .test-tools")
    args = p.parse_args()
    cores = Cores(ROOT / ".test-tools/cores")
    if args.download:
        for core in ("xray", "sing-box", "caddy"):
            destination = cores.stage(core)
            cores.switch(core, destination)
            print(core, json.loads((destination / "verified.json").read_text())["version"], "SHA-256/ELF/version OK")
    with tempfile.TemporaryDirectory(prefix="nodepilot-native-") as directory:
        base = Path(directory)
        private, public = cores.keypair()
        assert cores.keypair(private) == (private, public), "OpenSSL 密钥派生必须匹配官方 Xray 输出"
        for protocol in ("reality", "anytls", "hysteria2"):
            n = new_node(protocol, "native check", "8.8.8.8", port=14443, listen="127.0.0.1", sni="example.com")
            if protocol == "reality":
                import secrets, uuid
                n["reality"] = dict(private_key=private, public_key=public, uuid=str(uuid.uuid4()),
                    target="example.com:443", server_names=[n["sni"]], short_id=secrets.token_hex(8), fingerprint="chrome")
            else:
                n["tls"] = dict(mode="selfsigned", certificate="/nonexistent", key="/nonexistent")
                revision = private_dir(base / protocol)
                prepare(n, revision)
                if protocol == "hysteria2":
                    import secrets
                    n["obfs"] = {"type": "salamander", "password": secrets.token_urlsafe(32)}
                    n["up_mbps"], n["down_mbps"] = 10, 20
            config = base / (protocol + ".json")
            atomic_json(config, generate(n))
            cores.check(n, config)
            print(protocol, "official native config check OK")
        n = new_node("reality", "self target check", "8.8.8.8", port=14443, sni="example.com")
        n["reality"] = dict(private_key=private, public_key=public, uuid=str(uuid.uuid4()),
                            target="127.0.0.1:18003", server_names=[n["sni"]], short_id=secrets.token_hex(8),
                            fingerprint="chrome", mode="self")
        holder = {"sni": n["sni"], "tls": dict(mode="selfsigned", certificate="/nonexistent", key="/nonexistent")}
        prepare(holder, private_dir(base / "caddy-cert"))
        n["caddy"] = dict(domain=n["sni"], port=18003, certificate_mode="import", upstream="",
                          certificate=holder["tls"]["certificate"], key=holder["tls"]["key"])
        from nodepilot_pkg.common import run
        for mode in ("import", "acme", "dns01", "selfsigned"):
            n["caddy"]["certificate_mode"] = mode
            if mode=='dns01':
                n['caddy']['dns']={'zone_id':'0'*32,'credential_id':'cf-'+'0'*16,'propagation_seconds':60}
            config = base / ("caddy-" + mode + ".json")
            atomic_json(config, configuration([n], base))
            run([cores.binary("caddy"), "validate", "--config", config])
            print("caddy", mode, "official native config check OK")
        config = base / "self-reality.json"
        atomic_json(config, generate(n))
        cores.check(n, config)
        print("self-reality", "official native config check OK")
    print("配置检查通过；未执行公网连接或 256MiB 负载测试。")


if __name__ == "__main__":
    main()
