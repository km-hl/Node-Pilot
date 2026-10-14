"""Conservative conversion of supported standalone server profiles; never drop unknown behavior."""
import json

from .common import PilotError
from .model import host, key, new_node, validate


def keys(value, allowed, label):
    if not isinstance(value, dict) or set(value) - set(allowed):
        raise PilotError(f"{label} 含当前不能无损导入的字段；原服务保持不变。")


def parse_json(data):
    # JSONC comments/trailing commas are accepted without touching quoted URLs/keys.
    text = data.decode("utf-8-sig")
    out, i, quoted, escaped = [], 0, False, False
    while i < len(text):
        c = text[i]
        if quoted:
            out.append(c)
            if escaped:
                escaped = False
            elif c == "\\":
                escaped = True
            elif c == '"':
                quoted = False
            i += 1
            continue
        if c == '"':
            quoted = True
        elif text[i:i+2] == "//":
            end = text.find("\n", i)
            i = len(text) if end < 0 else end
            continue
        elif text[i:i+2] == "/*":
            end = text.find("*/", i + 2)
            if end < 0:
                raise PilotError("JSON 注释未闭合。")
            out.append(" ")
            i = end + 2
            continue
        elif c == ",":
            j = i + 1
            while j < len(text) and text[j].isspace():
                j += 1
            if j < len(text) and text[j] in "}]":
                i += 1
                continue
        out.append(c)
        i += 1
    clean, result, quoted, escaped = "".join(out), [], False, False
    for index, c in enumerate(clean):
        if not quoted and c == ",":
            j = index + 1
            while j < len(clean) and clean[j].isspace():
                j += 1
            if j < len(clean) and clean[j] in "}]":
                continue
        result.append(c)
        if escaped:
            escaped = False
        elif quoted and c == "\\":
            escaped = True
        elif c == '"':
            quoted = not quoted
    def unique(pairs):
        result = {}
        for name, value in pairs:
            if name in result:
                raise PilotError("原配置存在重复 JSON 字段，拒绝不明确的导入。")
            result[name] = value
        return result
    try:
        return json.loads("".join(result), object_pairs_hook=unique)
    except (ValueError, UnicodeError) as error:
        raise PilotError("原配置不是支持的 JSON/JSONC。") from error


def convert(config, core, server, keypair, adoption_id, snis=None):
    if core not in {"xray", "sing-box"}:
        raise PilotError("只接管独立 Xray/sing-box 服务。")
    keys(config, {"log", "inbounds", "outbounds", "routing", "policy"} if core == "xray" else
         {"log", "inbounds", "outbounds", "route"}, "原服务顶层配置")
    if core == "xray" and config.get("routing"):
        raise PilotError("原服务有路由规则，当前不能无损拆分，请保持原服务。")
    outbounds = config.get("outbounds", [])
    if len(outbounds) != 1:
        raise PilotError("只导入一个直接出站的标准服务；不丢弃其他出站。")
    outbound = outbounds[0]
    if core == "xray":
        keys(outbound, {"protocol", "tag", "settings"}, "Xray 出站")
        if outbound.get("protocol") != "freedom" or outbound.get("settings") not in (None, {}, {"domainStrategy": "AsIs"}):
            raise PilotError("原 Xray 出站不是标准 freedom。")
    else:
        keys(outbound, {"type", "tag"}, "sing-box 出站")
        if outbound.get("type") != "direct":
            raise PilotError("原 sing-box 出站不是标准 direct。")
        route = config.get("route", {})
        keys(route, {"final"}, "sing-box 路由")
        if route.get("final") and route["final"] != outbound.get("tag"):
            raise PilotError("原默认出站与 direct 不一致。")
    inbounds = config.get("inbounds", [])
    if not isinstance(inbounds, list) or not 1 <= len(inbounds) <= 16 or (snis is not None and len(snis) != len(inbounds)):
        raise PilotError("导入支持 1–16 个三协议入站，SNI 数量须一致。")
    nodes = []
    for index, inbound in enumerate(inbounds):
        if core == "xray":
            keys(inbound, {"tag", "listen", "port", "protocol", "settings", "streamSettings", "sniffing"}, "Xray 入站")
            if inbound.get("protocol") != "vless":
                raise PilotError("原服务含非 VLESS 入站，不能只迁移部分后停止原服务。")
            stream = inbound["streamSettings"]
            keys(stream, {"network", "security", "realitySettings"}, "Xray 传输")
            if stream.get("network", "tcp") not in {"tcp", "raw"} or stream.get("security") != "reality":
                raise PilotError("只导入 VLESS + TCP + Reality + Vision。")
            settings = inbound["settings"]
            keys(settings, {"clients", "decryption"}, "VLESS 设置")
            clients = settings.get("clients", [])
            if len(clients) != 1 or settings.get("decryption") != "none":
                raise PilotError("当前导入要求每个入站一个客户端、decryption=none。")
            client = clients[0]
            keys(client, {"id", "flow", "email", "level"}, "VLESS 客户端")
            if client.get("flow") != "xtls-rprx-vision" or client.get("level", 0) != 0:
                raise PilotError("只导入 Vision / level=0 客户端。")
            r = inbound["streamSettings"]["realitySettings"]
            keys(r, {"show", "target", "dest", "xver", "serverNames", "privateKey", "shortIds"}, "Reality 设置")
            if len(r.get("shortIds", [])) != 1 or not r.get("serverNames") or (r.get("target") and r.get("dest") and r["target"] != r["dest"]):
                raise PilotError("Reality 目标/shortIds 不明确，拒绝丢失其他认证方式。")
            sni = host(snis[index] if snis else r["serverNames"][0])
            n = new_node("reality", f"导入 Xray {index+1}", server, inbound["port"], inbound.get("listen") or "::", sni)
            n["adoption"] = adoption_id
            private, public = keypair(key(r["privateKey"]))
            n["reality"] = dict(mode="external", uuid=client["id"], private_key=private, public_key=public,
                short_id=r["shortIds"][0], server_names=r["serverNames"], target=r.get("target") or r.get("dest"),
                fingerprint="chrome", xver=r.get("xver", 0))
            if inbound.get("sniffing"):
                n["xray_sniffing"] = inbound["sniffing"]
            if config.get("policy"):
                n["xray_policy"] = config["policy"]
        else:
            protocol = inbound.get("type")
            if protocol not in {"anytls", "hysteria2"}:
                raise PilotError("原服务含非 AnyTLS/HY2 入站，拒绝只导入部分节点。")
            keys(inbound, {"tag", "type", "listen", "listen_port", "users", "tls", "obfs", "up_mbps", "down_mbps"}, "sing-box 入站")
            users = inbound.get("users", [])
            if len(users) != 1:
                raise PilotError("当前导入要求每个入站一个认证用户。")
            keys(users[0], {"name", "password"}, "认证用户")
            tls = inbound["tls"]
            keys(tls, {"enabled", "server_name", "min_version", "alpn", "certificate_path", "key_path"}, "TLS 设置")
            minimum = tls.get("min_version", "1.3" if protocol == "hysteria2" else "1.2")
            if tls.get("enabled") is not True or minimum not in {"1.2", "1.3"}:
                raise PilotError("导入需要启用正常 TLS；不接管 TLS 1.0/1.1 配置。")
            if tls.get("alpn") not in (None, ["h3"] if protocol == "hysteria2" else ["h2", "http/1.1"]):
                raise PilotError("原 ALPN 策略不能被当前模板无损保留。")
            sni = host(snis[index] if snis else tls.get("server_name", ""))
            n = new_node(protocol, f"导入 {protocol} {index+1}", server, inbound["listen_port"], inbound.get("listen") or "::", sni)
            n.update(adoption=adoption_id, password=users[0]["password"],
                     tls={"mode": "import", "certificate": tls["certificate_path"], "key": tls["key_path"], "min_version": minimum})
            if tls.get("alpn"):
                n["tls"]["alpn"] = tls["alpn"]
            if protocol == "hysteria2":
                for field in ("obfs", "up_mbps", "down_mbps"):
                    if field in inbound:
                        n[field] = inbound[field]
            elif any(field in inbound for field in ("obfs", "up_mbps", "down_mbps")):
                raise PilotError("AnyTLS 入站含不支持的 HY2 设置。")
        validate(n)
        nodes.append(n)
    return nodes
