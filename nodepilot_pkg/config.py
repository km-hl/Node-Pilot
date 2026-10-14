"""Pure official-core server configuration generators."""
from .model import validate


def generate(n):
    validate(n)
    if n["protocol"] == "reality":
        r = n["reality"]
        return {"log": {"loglevel": "warning"}, "inbounds": [{
            "tag": n["id"], "listen": n["listen"], "port": n["port"],
            "protocol": "vless", "settings": {"decryption": "none", "clients": [{
                "id": r["uuid"], "flow": "xtls-rprx-vision"}]},
            "streamSettings": {"network": "tcp", "security": "reality", "realitySettings": {
                "show": False, "target": r["target"], "xver": 1 if r.get("mode") == "self" else 0,
                "serverNames": r["server_names"], "privateKey": r["private_key"],
                "shortIds": [r["short_id"]]}}}],
            "outbounds": [{"protocol": "freedom", "tag": "direct"}]}
    inbound = {"type": n["protocol"], "tag": n["id"], "listen": n["listen"],
               "listen_port": n["port"], "users": [{"name": n["id"], "password": n["password"]}],
               "tls": {"enabled": True, "server_name": n["sni"],
                       "min_version": "1.3", "certificate_path": n["tls"]["certificate"],
                       "key_path": n["tls"]["key"]}}
    if n["protocol"] == "hysteria2":
        inbound["tls"]["alpn"] = ["h3"]
        if n.get("obfs"):
            inbound["obfs"] = n["obfs"]
        for f in ("up_mbps", "down_mbps"):
            if f in n:
                inbound[f] = n[f]
    return {"log": {"level": "warn", "timestamp": True}, "inbounds": [inbound],
            "outbounds": [{"type": "direct", "tag": "direct"}]}
