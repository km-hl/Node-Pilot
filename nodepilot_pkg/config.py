"""Pure official-core server configuration generators."""
from .model import split_target, validate


def generate(n):
    validate(n)
    if n["protocol"] == "reality":
        r = n["reality"]
        target_host, target_port = split_target(r["target"])
        inbound = {
            "type": "vless",
            "tag": n["id"],
            "listen": n["listen"],
            "listen_port": n["port"],
            "users": [{"uuid": r["uuid"], "flow": "xtls-rprx-vision"}],
            "tls": {
                "enabled": True,
                "server_name": n["sni"],
                "reality": {
                    "enabled": True,
                    "handshake": {"server": target_host, "server_port": target_port},
                    "private_key": r["private_key"],
                    "short_id": [r["short_id"]],
                    "max_time_difference": "1m",
                },
            },
        }
        return {"log": {"level": "warn", "timestamp": True}, "inbounds": [inbound],
                "outbounds": [{"type": "direct", "tag": "direct"}]}
    inbound = {"type": n["protocol"], "tag": n["id"], "listen": n["listen"],
               "listen_port": n["port"], "users": [{"name": n["id"], "password": n["password"]}],
               "tls": {"enabled": True, "server_name": n["sni"],
                       "min_version": n["tls"].get("min_version", "1.3"), "certificate_path": n["tls"]["certificate"],
                       "key_path": n["tls"]["key"]}}
    if n["tls"].get("alpn"):
        inbound["tls"]["alpn"] = n["tls"]["alpn"]
    if n["protocol"] == "hysteria2":
        inbound["tls"]["alpn"] = ["h3"]
        if n.get("obfs"):
            inbound["obfs"] = n["obfs"]
        for f in ("up_mbps", "down_mbps"):
            if f in n:
                inbound[f] = n[f]
    return {"log": {"level": "warn", "timestamp": True}, "inbounds": [inbound],
            "outbounds": [{"type": "direct", "tag": "direct"}]}
