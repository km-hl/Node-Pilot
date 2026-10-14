"""Versioned node metadata. Credentials are stored only in private node revisions."""
import base64
import ipaddress
import re
import secrets
import uuid
from datetime import datetime, timezone

from .common import PilotError

PROTOCOLS = {"reality": ("xray", "tcp"), "anytls": ("sing-box", "tcp"),
             "hysteria2": ("sing-box", "udp")}
ID = re.compile(r"n-[0-9a-f]{16}\Z")


def host(value, public=False):
    if not isinstance(value, str) or not value or len(value) > 253:
        raise PilotError("地址不能为空或过长。")
    try:
        ip = ipaddress.ip_address(value)
        if "%" in value:
            raise PilotError("不支持带 scope ID 的 IP 地址。")
        if public and not ip.is_global:
            raise PilotError("分享地址必须是公网 IP 或域名，不能自动使用私网地址。")
        return str(ip)
    except ValueError:
        labels = value.rstrip(".").split(".")
        if len(labels) < 2 or any(not re.fullmatch(r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?", x) for x in labels):
            raise PilotError("请输入合法 IP 或 ASCII 域名（国际域名使用 punycode）。")
        if public and value.lower().endswith((".local", ".localhost", ".invalid", ".test")):
            raise PilotError("不能使用本地域名作为公网分享地址。")
        return value.lower().rstrip(".")


def key(value):
    try:
        if not re.fullmatch(r"[A-Za-z0-9_-]{43}", value):
            raise ValueError()
        raw = base64.urlsafe_b64decode(value + "=")
        if len(raw) != 32:
            raise ValueError()
        return value
    except (ValueError, TypeError) as e:
        raise PilotError("Reality 密钥必须为 32 字节的 base64url 编码。") from e


def validate(n):
    if n.get("schema") != 1 or not ID.fullmatch(n.get("id", "")):
        raise PilotError("不支持的数据格式或非法节点 ID。")
    if n.get("protocol") not in PROTOCOLS:
        raise PilotError("只支持 reality、anytls、hysteria2。")
    core, transport = PROTOCOLS[n["protocol"]]
    if n.get("core") != core or n.get("transport") != transport or n.get("service") != f"nodepilot-{n['id']}.service":
        raise PilotError("节点内核、传输方式或服务关联不一致。")
    if not isinstance(n.get("name"), str) or not 1 <= len(n["name"]) <= 80 or any(ord(c) < 32 or ord(c) == 127 for c in n["name"]):
        raise PilotError("名称长度为 1–80，不能包含控制字符。")
    if type(n.get("port")) is not int or not 1 <= n["port"] <= 65535:
        raise PilotError("端口必须在 1–65535 之间。")
    try:
        ipaddress.ip_address(n["listen"])
    except (KeyError, ValueError) as e:
        raise PilotError("监听地址必须是 IP；推荐 0.0.0.0 或 ::。") from e
    host(n["server"], public=True)
    host(n["sni"])
    if n["protocol"] == "reality":
        r = n["reality"]
        try:
            uuid.UUID(r["uuid"])
        except (ValueError, AttributeError, TypeError) as e:
            raise PilotError("Reality UUID 格式无效。") from e
        key(r["private_key"])
        key(r["public_key"])
        if not re.fullmatch(r"(?:[0-9a-fA-F]{2}){1,8}", r["short_id"]):
            raise PilotError("shortId 必须为 2–16 位偶数长度十六进制。")
        if not r["server_names"] or n["sni"] not in r["server_names"]:
            raise PilotError("客户端 SNI 必须包含在 Reality serverNames 中。")
        for s in r["server_names"]:
            host(s)
        target_host, target_port = split_target(r["target"])
        host(target_host)
        if not 1 <= target_port <= 65535:
            raise PilotError("Reality target 端口无效。")
        if r["fingerprint"] not in {"chrome", "firefox", "safari", "edge", "ios", "android", "random", "randomized"}:
            raise PilotError("不支持的客户端 uTLS fingerprint。")
    else:
        if not isinstance(n.get("password"), str) or len(n["password"]) < 16 or any(ord(c) < 32 for c in n["password"]):
            raise PilotError("密码至少 16 字符且不能包含控制字符。")
        tls = n["tls"]
        if tls.get("mode") not in {"import", "acme", "selfsigned"}:
            raise PilotError("非法证书模式。")
        if not tls.get("certificate") or not tls.get("key"):
            raise PilotError("缺少 TLS 证书或私钥路径。")
        if tls["mode"] == "acme" and transport == "tcp" and n["port"] == 80:
            raise PilotError("HTTP-01 需要空闲 TCP 80；ACME 节点请使用其他 TCP 端口。")
        if n["protocol"] == "hysteria2":
            if n.get("obfs") and (n["obfs"].get("type") != "salamander" or len(n["obfs"].get("password", "")) < 16):
                raise PilotError("仅支持 salamander，混淆密码至少 16 字符。")
            for f in ("up_mbps", "down_mbps"):
                if f in n and (type(n[f]) is not int or not 1 <= n[f] <= 100000):
                    raise PilotError("带宽提示必须为 1–100000 Mbps 的整数。")
    return n


def split_target(value):
    try:
        h, p = value.rsplit(":", 1)
        return h.strip("[]"), int(p)
    except (AttributeError, ValueError) as e:
        raise PilotError("Reality target 格式：域名:端口 或 [IPv6]:端口。") from e


def new_node(protocol, name, server, port=443, listen="0.0.0.0", sni=""):
    if protocol not in PROTOCOLS:
        raise PilotError("不支持的协议。")
    node_id = "n-" + secrets.token_hex(8)
    core, transport = PROTOCOLS[protocol]
    n = dict(schema=1, id=node_id, name=name, protocol=protocol, core=core,
             transport=transport, server=host(server, True), port=port,
             listen=listen, sni=sni, service=f"nodepilot-{node_id}.service",
             created_at=datetime.now(timezone.utc).isoformat())
    if protocol != "reality":
        n["password"] = secrets.token_urlsafe(32)
    return n
