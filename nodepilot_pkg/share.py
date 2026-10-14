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
        if n["protocol"] == "hysteria2" and n.get("obfs"):
            q.update({"obfs": "salamander", "obfs-password": n["obfs"]["password"]})
    return f"{scheme}://{quote(auth, safe='')}@{server}:{n['port']}{path}?{urlencode(q, quote_via=quote)}#{quote(n['name'], safe='')}"


def display(n):
    lines = ["注意：以下文本含认证凭据，请仅在可信终端查看和保存。",
             f"名称：{n['name']}", f"ID：{n['id']}", f"协议：{n['protocol']} ({n['transport']})",
             f"服务器：{n['server']}", f"端口：{n['port']}", f"SNI：{n['sni']}",
             "UUID：" + n["reality"]["uuid"] if n["protocol"] == "reality" else "密码：" + n["password"],
             share(n)]
    if n["protocol"] != "reality" and n["tls"]["mode"] == "selfsigned":
        lines.append("测试自签名：必须在客户端导入此节点 certificate.pem 为信任证书；URI 单独导入不能建立信任。")
    if n["protocol"] == "anytls":
        lines.append("AnyTLS 需要支持该协议和 URI 的客户端；旧版 Clash/v2rayN 不一定支持。")
    return "\n".join(lines)
