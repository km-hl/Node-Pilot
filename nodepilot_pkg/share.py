"""URI encoding according to the upstream protocol specifications."""
import ipaddress
from urllib.parse import quote, urlencode

from .model import validate


def share(n):
    validate(n)
    server = n["server"]
    try:
        if ipaddress.ip_address(server).version == 6:
            server = f"[{server}]"
    except ValueError:
        pass
    q = {"sni": n["sni"]}
    if n["protocol"] == "reality":
        r = n["reality"]
        auth, scheme, path = r["uuid"], "vless", ""
        q.update(type="tcp", security="reality", encryption="none", flow="xtls-rprx-vision",
                 fp=r["fingerprint"], pbk=r["public_key"], sid=r["short_id"])
    else:
        auth, scheme, path = n["password"], n["protocol"], "/"
        # Self-signed certificates require importing the CA in clients. Never set insecure=1.
        q["insecure"] = "0"
        if n["protocol"] == "hysteria2":
            if n.get("obfs"):
                q.update({"obfs": "salamander", "obfs-password": n["obfs"]["password"]})
            if n.get("mport"):
                q["mport"] = n["mport"]
    return f"{scheme}://{quote(auth, safe='')}@{server}:{n['port']}{path}?{urlencode(q, quote_via=quote)}#{quote(n['name'], safe='')}"


def display(n, show_qr=False, compact_qr=True):
    uri = share(n)
    lines = ["注意：以下文本含认证凭据，请仅在可信终端查看和保存。",
             f"名称：{n['name']}", f"ID：{n['id']}", f"协议：{n['protocol']} ({n['transport']})",
             f"内核：{n['core']}",
             f"服务器：{n['server']}", f"端口：{n['port']}", f"SNI：{n['sni']}"]
    if n.get("mport"):
        lines.append(f"端口跳跃：{n['mport']}（间隔 {n.get('hop_interval', 30)}s；需自行配置防火墙 UDP 转发到 {n['port']}）")
    lines.extend(["UUID：" + n["reality"]["uuid"] if n["protocol"] == "reality" else "密码：" + n["password"], uri])
    if n["protocol"] != "reality" and n["tls"]["mode"] == "selfsigned":
        lines.append("测试自签名：必须在客户端导入此节点 certificate.pem 为信任证书；URI 单独导入不能建立信任。")
    if n["protocol"] == "anytls":
        lines.append("AnyTLS 兼容性：请使用 sing-box 1.12+ / Mihomo v1.19.24+ / 新版 v2rayN / Egern；不支持的旧客户端请勿关闭 TLS 验证。")
    elif n["protocol"] == "reality":
        lines.append("Reality 兼容性：Mihomo 请使用菜单 10 导出（含 packet-encoding: xudp 与 support-x25519mlkem768）。")
    if show_qr:
        from . import qr
        try:
            lines.extend(["", "终端二维码：", qr.render(uri, compact=compact_qr)])
        except ValueError as err:
            lines.append(f"二维码未生成：{err}")
    return "\n".join(lines)
