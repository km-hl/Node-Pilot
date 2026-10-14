"""Plain text SSH TUI. All input comes from /dev/tty, including curl pipelines."""
import copy
import os
import secrets
import shutil
import sys
import uuid
from pathlib import Path

from .common import PilotError, run
from .cores import Cores
from .manager import Manager
from .model import email, host, is_ip, new_node, validate
from .share import display
from .system import architecture, diagnostics, existing_engines, public_ips, suggested_listen, supported


class Menu:
    def __init__(self):
        try:
            # TextIOWrapper r+ attempts seeking; a terminal is not seekable.
            self.tty_read = open("/dev/tty", "r", buffering=1, encoding="utf-8")
            self.tty = open("/dev/tty", "w", buffering=1, encoding="utf-8")
        except OSError as e:
            raise PilotError("需要交互终端；请通过 SSH 分配 TTY（ssh -t），离线生成使用 render 子命令。") from e
        self.manager = Manager()
        self.system = supported()
        self.ips = public_ips()

    def say(self, value=""):
        print(value, file=self.tty, flush=True)

    def ask(self, prompt, default=None):
        self.tty.write(prompt + (f" [{default}]" if default is not None else "") + "：")
        self.tty.flush()
        line = self.tty_read.readline()
        if not line:
            raise EOFError()
        value = line.strip()
        return str(default) if not value and default is not None else value

    def secret(self, prompt, current=None):
        import getpass
        value = getpass.getpass(prompt + ("（留空保留/自动生成）" if current else "（留空自动生成）") + "：", stream=self.tty)
        return value or current or secrets.token_urlsafe(32)

    def confirm(self, text, default=False):
        while True:
            value = self.ask(text + "（y/n）", "y" if default else "n").lower()
            if value in {"y", "n"}:
                return value == "y"
            self.say("请输入 y 或 n，支持 Y/N；回车采用提示中的默认值。")

    def select(self):
        self.list_nodes()
        node_id = self.ask("节点 ID")
        return self.manager.get(node_id)

    def list_nodes(self):
        nodes = self.manager.nodes()
        if not nodes:
            self.say("尚无节点。")
        for n in nodes:
            state = "运行中" if self.manager.services.active(n) else "未运行"
            from .caddy import own_target
            if own_target(n) and not self.manager.caddy.active():
                state = "Caddy 目标未运行"
            self.say(f"{n['id']}  {n['name']}\n  {n['protocol']} {n['listen']}:{n['port']}/{n['transport']}  {state}")

    def ensure_core(self, core):
        try:
            self.manager.cores.binary(core)
        except PilotError:
            if not self.confirm(f"从官方稳定版安装独立 {core} 内核（SHA-256 校验）"):
                raise PilotError("已取消内核安装。")
            selected = self.choose_version(core)
            self.say("正在下载、校验并安装官方内核……")
            with self.manager.locked():
                self.manager.update_core(core, selected)

    def choose_version(self, core):
        self.say("版本：latest 官方最新稳定版；list 查看稳定版本；也可输入完整版本号；0 取消。")
        while True:
            selected = self.ask(f"{core} 安装版本", "latest")
            if selected == "0":
                raise PilotError("已取消版本选择。")
            if selected.lower() == "list":
                from .project_update import project_versions
                tags = project_versions() if core == "nodepilot" else self.manager.cores.versions(core)
                self.say("可选稳定版本：" + (", ".join(tags) or "暂无符合最低版本要求的公开版本"))
                continue
            if selected.lower() == "latest":
                return "latest"
            from .releases import MINIMUM, version
            if version(selected) < (MINIMUM[core] if core != "nodepilot" else (0, 3, 0)):
                self.say("版本低于当前功能支持范围，请选择较新版本。")
                continue
            return selected

    def check_updates(self):
        from . import __version__
        from .project_update import check_update
        from .releases import REPOSITORIES, release, version
        self.say("正在检查官方稳定版；检查过程不会下载安装或重启节点。")
        for core, repo in REPOSITORIES.items():
            try:
                latest = release(repo)["tag_name"]
                try:
                    current = self.manager.cores.installed_version(core)
                except PilotError:
                    current = "未安装"
                state = "未安装" if current == "未安装" else "可更新" if version(latest) > version(current) else "已是最新或更高版本"
                self.say(f"{core}：当前 {current}；最新 {latest}；{state}")
            except PilotError as error:
                self.say(f"{core}：检查失败：{error}")
        try:
            latest = check_update()["tag_name"]
            self.say(f"NodePilot：当前 v{__version__}；最新 {latest}；" + ("可更新" if version(latest) > version(__version__) else "已是最新或更高版本"))
        except PilotError as error:
            self.say(f"NodePilot：检查失败：{error}")

    def adoption(self):
        from .adoption import Adoptions, SourceService
        from .importer import parse_json
        import base64
        controller = Adoptions(self.manager)
        choice = self.ask("1 检查并接管现有独立内核  2 归还原服务  3 查看接管记录  0 返回", "0")
        if choice == "0":
            return
        if choice in {"2", "3"}:
            records = controller.records()
            for record in records:
                self.say(f"{record['id']}  {record['source']['service']}  {record['phase']}  {len(record['nodes'])} 个节点")
            if choice == "3":
                return
            identity = self.ask("接管记录 ID")
            record = controller.load(identity)
            if self.confirm(f"归还 {record['source']['service']}：移除其接管节点，恢复原启用/运行状态") and self.confirm("再次确认归还；其他 NodePilot 节点保留"):
                with self.manager.locked():
                    controller.restore(identity, confirmed=True)
                self.say("原服务已归还；原配置文件保留，私有接管备份仍可查看。")
            return
        if choice != "1":
            raise PilotError("无效接管选项。")
        self.say("仅支持独立 Xray/sing-box 的三协议标准配置；面板、复杂路由、多配置/多用户不会部分迁移。")
        for engine in existing_engines():
            self.say(f"发现 {engine['name']}，服务 {engine['service'] or '无独立 systemd 服务'}")
        service = self.ask("原服务名称（如 xray.service / sing-box.service）")
        snapshot = SourceService().inspect(service)
        raw = next(f for f in snapshot["files"] if f["path"] == snapshot["config"])
        config = parse_json(base64.b64decode(raw["data"], validate=True))
        snis = []
        for index, inbound in enumerate(config.get("inbounds", []), 1):
            r = inbound.get("streamSettings", {}).get("realitySettings", {})
            suggested = (r.get("serverNames") or [inbound.get("tls", {}).get("server_name", "")])[0]
            snis.append(host(self.ask(f"原入站 {index} 的客户端 SNI", suggested or None)))
        server = host(self.ask("公网分享 IP/域名", self.ips["ipv4"] or self.ips["ipv6"] or None), True)
        self.ensure_core(snapshot["core"])
        with self.manager.locked():
            record = controller.prepare(service, server, snis)
        self.say(f"原服务：{service}；配置只复制备份，不覆盖。原运行状态：{'active' if record['source']['was_active'] else '未运行'}；启用状态：{record['source']['enabled']}")
        self.say("日志转入独立 NodePilot journal；原认证、监听和 SNI 保留。原服务切换后停用，记录可归还。")
        self.say(f"将启动 {len(record['nodes'])} 个独立内核进程；多入站拆分会增加内存占用，低内存机器请先评估。")
        for node in record["nodes"]:
            self.say(f"{node['name']}  {node['protocol']}  {node['listen']}:{node['port']}/{node['transport']}  SNI={node['sni']}")
            if (node["protocol"] == "reality" and not node["reality"]["short_id"]) or (node["protocol"] != "reality" and len(node["password"]) < 16):
                self.say("原节点认证参数较弱，会为兼容原客户端保留；建议接管后更换。")
        self.say(f"已完成官方内核配置检查；私有备份：{controller.path(record['id'])}")
        if not self.confirm(f"是否接管 {service}，切换以上全部节点到 NodePilot") or not self.confirm("再次确认：允许停止并停用所选原服务；失败恢复原状态"):
            self.say("已取消，原服务保持不变。")
            return
        with self.manager.locked():
            controller.execute(record["id"], confirmed=True)
        self.say("已接管，节点可在菜单 4–8 管理；菜单 13 可归还原服务。尚需真实客户端验证。")

    def tls_input(self, n, old=None):
        if old and not self.confirm("是否更换证书来源/重新生成测试证书"):
            n["tls"] = copy.deepcopy(old["tls"])
            return False
        self.say("证书方式：1 导入现有 PEM（含完整链）  2 Let's Encrypt HTTP-01  3 测试自签名")
        choice = self.ask("选择", "1")
        if choice == "1":
            n["tls"] = {"mode": "import", "certificate": self.ask("证书绝对路径"), "key": self.ask("无口令私钥绝对路径")}
        elif choice == "2":
            self.say("域名所有 A/AAAA 必须直指本机公网 IP。HTTP-01 需要公网 TCP 80，云安全组/防火墙需手动放行；不会停止已有 Web 服务。")
            contact = email(self.ask("ACME 联系邮箱（可留空）"))
            if not self.confirm("确认 DNS、HTTP-01、TCP 80 可达，并同意 Let's Encrypt 服务条款"):
                raise PilotError("已取消证书申请。")
            if not shutil.which("certbot"):
                if not self.confirm("通过系统签名软件源安装 certbot 依赖"):
                    raise PilotError("已取消 certbot 安装。")
                run(["apt-get", "update"], timeout=240)
                run(["apt-get", "install", "-y", "--no-install-recommends", "certbot"], timeout=300)
            n["tls"] = {"mode": "acme", "domain": n["sni"], "email": contact,
                        "certificate": "/nonexistent/nodepilot-cert", "key": "/nonexistent/nodepilot-key"}
        elif choice == "3":
            if not self.confirm("自签名仅供测试；客户端必须手动信任 certificate.pem，分享链接保持证书验证"):
                raise PilotError("已取消测试证书。")
            n["tls"] = {"mode": "selfsigned", "certificate": "/nonexistent/nodepilot-cert", "key": "/nonexistent/nodepilot-key"}
            return True
        else:
            raise PilotError("无效证书方式。")
        return False

    def reality_input(self, n, old):
        from .caddy import own_target
        from .system import port_free
        from .targets import CANDIDATES, cloudflare_networks, inspect_target, rank_targets
        previous = old if old and own_target(old) else None
        self.say("推荐：自有域名 + Caddy 自动证书 + 本机 Reality 目标。没有域名再选第三方目标。")
        domain = self.ask("自有域名（留空表示没有域名；输入 - 切换到无域名）", previous["sni"] if previous else "")
        r = copy.deepcopy(old["reality"]) if old else {}
        if domain and domain != "-":
            r.pop("selection", None)
            domain = host(domain, public=True)
            if is_ip(domain):
                raise PilotError("请输入自己控制的域名。")
            self.ensure_core("caddy")
            n["sni"] = domain
            c = copy.deepcopy(previous["caddy"]) if previous else {}
            c["domain"] = domain
            suggested = c.get("port", 8003)
            if not previous:
                reserved = {o["port"] for o in self.manager.nodes() if o["transport"] == "tcp"}
                reserved |= {o["caddy"]["port"] for o in self.manager.nodes() if own_target(o)}
                for candidate in range(8003, 8203):
                    if candidate in reserved or candidate == n["port"]:
                        continue
                    try:
                        port_free("127.0.0.1", candidate, "tcp")
                        suggested = candidate
                        break
                    except PilotError:
                        continue
                else:
                    raise PilotError("未找到空闲本机目标端口，请检查监听。")
            c["port"] = int(self.ask("Caddy 本机目标端口", suggested))
            source = self.ask("网页来源 HTTPS 域名（留空或 - 使用本机欢迎页，节省资源）", c.get("upstream", "").removesuffix(":443"))
            c["upstream"] = "" if source in {"", "-"} else host(source, public=True) + ":443"
            if not previous or domain != previous["sni"] or self.confirm("更换 Caddy 证书来源"):
                choice = self.ask("Caddy 证书：1 自动申请/续期（默认）  2 导入现有 PEM", "1")
                if choice == "1":
                    self.say("域名全部 A/AAAA 必须直指 VPS；HTTP-01 需公网 TCP 80。不停止已有网站服务，443 由 Xray 使用。")
                    c["email"] = email(self.ask("ACME 邮箱（可留空）"))
                    if not self.confirm("确认域名归属、DNS、TCP 80 可达，并同意 Let's Encrypt 服务条款"):
                        raise PilotError("已取消证书申请。")
                    c["certificate_mode"] = "acme"
                    c.pop("certificate", None)
                    c.pop("key", None)
                elif choice == "2":
                    c.update(certificate_mode="import", certificate=self.ask("证书完整链 PEM 绝对路径"),
                             key=self.ask("无口令私钥 PEM 绝对路径"))
                else:
                    raise PilotError("无效 Caddy 证书方式。")
            n["caddy"] = c
            r.update(mode="self", target=f"127.0.0.1:{c['port']}", server_names=[domain])
        else:
            n.pop("caddy", None)
            self.say("无域名：从本 VPS 测量 TCP+TLS 握手时延，排除 Cloudflare CDN、跳转、无有效证书/TLS 1.3/h2 的站点。")
            candidates = self.ask("候选域名（逗号分隔；留空使用内置；keep 保留原目标；manual 自定义 target/SNI）", "")
            if candidates == "keep" and old and not own_target(old):
                n["sni"] = old["sni"]
            elif candidates == "manual":
                sni = host(self.ask("第三方客户端 SNI"), public=True)
                target = self.ask("第三方 Reality 目标 host:port", sni + ":443")
                selected = inspect_target(target, sni, cloudflare_networks())
                self.say(f"已通过非 CF/TLS/网页检查，当前 TCP+TLS 中位时延 {selected['median_ms']} ms。")
                n["sni"] = sni
                r.update(target=target, server_names=[sni], selection=selected)
                r["xver"] = 0
            else:
                ranked = rank_targets([host(x.strip(), True) for x in candidates.split(",")] if candidates else CANDIDATES, self.say)
                if not ranked:
                    raise PilotError("没有合格的非 CF 目标；请重新提供候选域名。不会自动选择未通过检查的 SNI。")
                for index, result in enumerate(ranked, 1):
                    self.say(f"{index}. {result['sni']}  {result['median_ms']} ms")
                index = int(self.ask("选择目标（默认当前测量时延最低）", "1"))
                if not 1 <= index <= len(ranked):
                    raise PilotError("目标序号无效。")
                selected = ranked[index - 1]
                n["sni"] = selected["sni"]
                r.update(target=selected["target"], server_names=[selected["sni"]], selection=selected)
                r["xver"] = 0
            r["mode"] = "external"
            self.say("测量只说明 VPS 到目标的当前握手时延；选站后还需真实客户端验证 Reality 联通。")
        r["uuid"] = self.ask("UUID（留空保留/自动生成）", r.get("uuid", str(uuid.uuid4())))
        r["short_id"] = self.ask("shortId", r.get("short_id", secrets.token_hex(8)))
        r["fingerprint"] = self.ask("客户端 uTLS fingerprint", r.get("fingerprint", "chrome"))
        if not old or self.confirm("更换 Reality x25519 密钥"):
            private = self.secret("Reality 私钥：输入已有私钥，或输入 auto 生成", "auto")
            r["private_key"], r["public_key"] = self.manager.cores.keypair(None if private == "auto" else private)
        n["reality"] = r

    def node_input(self, protocol, old=None):
        self.ensure_core("xray" if protocol == "reality" else "sing-box")
        server = host(self.ask("公网分享 IP/域名", old["server"] if old else self.ips["ipv4"] or self.ips["ipv6"] or None), True)
        name = self.ask("节点名称", old["name"] if old else protocol)
        port = int(self.ask("监听端口", old["port"] if old else (8443 if protocol == "anytls" else 443)))
        listen = self.ask("监听 IP", old["listen"] if old else suggested_listen(server, self.ips))
        sni = (old["sni"] if old else "") if protocol == "reality" else host(self.ask("客户端 TLS SNI", old["sni"] if old else None))
        n = copy.deepcopy(old) if old else new_node(protocol, name, server, port, listen, sni)
        n.update(name=name, server=server, port=port, listen=listen, sni=sni)
        renew = False
        if protocol == "reality":
            self.reality_input(n, old)
        else:
            n["password"] = self.secret("认证密码", old["password"] if old else None)
            renew = self.tls_input(n, old)
            if protocol == "hysteria2":
                if self.confirm("启用 salamander 混淆", default=bool(old and old.get("obfs"))):
                    n["obfs"] = {"type": "salamander", "password": self.secret("混淆密码", old.get("obfs", {}).get("password") if old else None)}
                else:
                    n.pop("obfs", None)
                self.say("带宽值是拥塞控制提示，不能增加实际带宽，也不等同于链路限速；留空使用内核默认。")
                for field in ("up_mbps", "down_mbps"):
                    value = self.ask(field, old.get(field, "") if old else "")
                    if value:
                        n[field] = int(value)
                    else:
                        n.pop(field, None)
        validate(n)
        return n, renew

    def create(self, protocol):
        n, renew = self.node_input(protocol)
        with self.manager.locked():
            saved = self.manager.apply(n, renew=renew)
        self.say(display(saved))
        self.say(f"请手动检查防火墙/云安全组放行 {saved['port']}/{saved['transport']}；尚未证明客户端联通。")

    def edit(self, old=None):
        old = old or self.select()
        n, renew = self.node_input(old["protocol"], old)
        with self.manager.locked():
            # Prevent overwriting a newer revision after time spent in interactive prompts.
            if self.manager.get(old["id"]) != old:
                raise PilotError("节点已被另一会话修改；请重新进入修改流程。")
            saved = self.manager.apply(n, renew=renew)
        self.say(display(saved))

    def delete(self):
        n = self.select()
        if self.confirm(f"删除 {n['name']} ({n['id']})，仅停止其独立服务"):
            if not self.confirm(f"再次确认删除节点 {n['name']} ({n['id']})，其他节点保留"):
                self.say("已取消删除。")
                return
            with self.manager.locked():
                backup = self.manager.delete(n["id"])
            self.say(f"已删除；含凭据的备份：{backup}")

    def status(self):
        n = self.select()
        self.say("服务状态：" + ("运行中" if self.manager.services.active(n) else "未运行"))
        action = self.ask("1 查看日志（可能含凭据）  2 重启此节点  3 NodePilot Caddy 日志  0 返回", "0")
        if action == "1":
            self.say(self.manager.services.logs(n))
        elif action == "2":
            with self.manager.locked():
                self.manager.restart(n)
            self.say("节点已重启。")
        elif action == "3":
            self.manager.caddy.assert_owned()
            self.say(run(["journalctl", "-u", "nodepilot-caddy.service", "-n", "60", "--no-pager"]))

    def update(self):
        self.say("1 安装/更新 Xray  2 安装/更新 sing-box  3 更新 NodePilot  4 安装/更新 Caddy\n5 检查全部版本更新  0 返回")
        choice = self.ask("选择")
        if choice == "0":
            return
        if choice == "5":
            self.check_updates()
            return
        if choice == "3":
            from .project_update import online_update
            selected = self.choose_version("nodepilot")
            if self.confirm("从已记录的本项目 GitHub 仓库下载稳定版，校验安装器 SHA-256 后升级"):
                online_update(self.tty_read, self.tty, selected)
                raise EOFError()
            return
        core = {"1": "xray", "2": "sing-box", "4": "caddy"}.get(choice)
        if not core:
            raise PilotError("无效选项。")
        selected = self.choose_version(core)
        if self.confirm(f"安装/更新官方 {core}，校验所有受影响节点并逐个重启"):
            with self.manager.locked():
                destination = self.manager.update_core(core, selected)
            self.say(f"已安装并验证：{destination.name}")

    def backup_restore(self):
        choice = self.ask("1 备份  2 恢复到空节点目录  3 更新/续期证书")
        if choice == "1":
            with self.manager.locked():
                self.say(f"私有备份（含密码和私钥）：{self.manager.backup()}")
        elif choice == "2":
            backup = self.ask("备份 JSON 绝对路径")
            if self.confirm("恢复可信备份；证书恢复为导入模式，需要重新配置 ACME 才能自动续期"):
                with self.manager.locked():
                    self.say(f"已恢复 {len(self.manager.restore(backup))} 个节点。")
        elif choice == "3":
            n = self.select()
            if n["protocol"] == "reality":
                from .caddy import own_target
                if not own_target(n):
                    raise PilotError("第三方 Reality 目标没有本地证书；自建目标使用菜单 5 修改证书，Caddy 自动续期。")
                self.edit(n)
                return
            n = copy.deepcopy(n)
            renew = self.tls_input(n)
            with self.manager.locked():
                self.manager.apply(n, renew=renew)
            self.say("证书配置已更新。")
        else:
            raise PilotError("无效选项。")

    def uninstall(self):
        if not self.confirm("卸载 NodePilot，删除全部节点和本项目程序，保留私有备份和其他服务"):
            return
        if not self.confirm("再次确认卸载并删除全部 NodePilot 节点，私有备份保留"):
            self.say("已取消卸载。")
            return
        from .installation import uninstall
        with self.manager.locked():
            backup = uninstall(self.manager)
        self.say(f"已卸载，备份保留在 {backup}。可在检查后自行清理。")
        raise EOFError()

    def start(self):
        with self.manager.locked():
            self.manager.recover_core()
            self.manager.recover()
            from .adoption import Adoptions
            Adoptions(self.manager).recover()
        for core in ("xray", "sing-box"):
            for path in (f"/etc/{core}", "/etc/s-box", f"/usr/local/bin/{core}", f"/usr/bin/{core}"):
                if Path(path).exists():
                    self.say(f"检测到已有 {path}，NodePilot 使用独立目录。")
        for engine in existing_engines():
            self.say(f"发现现有内核进程：{engine['name']} (PID {engine['pid']}, {engine['service'] or '未关联 systemd 单元'})；菜单 13 可选择是否接管，默认保留。")
        while True:
            self.say("\n================================\n      NodePilot 节点管理器\n================================")
            self.say(f"系统：{self.system}\n架构：{architecture()}\n公网 IPv4：{self.ips['ipv4'] or '未探测到'}\n公网 IPv6：{self.ips['ipv6'] or '未探测到'}")
            for core in ("xray", "sing-box"):
                try:
                    self.manager.cores.binary(core)
                    nodes = [n for n in self.manager.nodes() if n["core"] == core]
                    status = f"已安装，运行 {sum(self.manager.services.active(n) for n in nodes)}/{len(nodes)} 节点"
                except PilotError:
                    status = "未安装"
                self.say(f"NodePilot {core}：{status}")
            from .caddy import UNIT
            if (self.manager.services.units / UNIT).exists():
                self.manager.caddy.assert_owned()
                self.say("NodePilot Caddy：" + ("运行中" if self.manager.caddy.active() else "未运行"))
            from . import __version__
            self.say(f"NodePilot 版本：{__version__}")
            self.say("1. 安装 VLESS + Reality\n2. 安装 AnyTLS\n3. 安装 Hysteria2\n4. 查看全部节点\n5. 修改节点配置\n6. 删除指定节点\n7. 查看分享链接\n8. 服务状态/日志/重启\n9. 安装/更新内核与脚本（可选版本）\n10. 备份/恢复/证书\n11. 端口与网络诊断\n12. 卸载 NodePilot\n13. 接管/归还现有 Xray 或 sing-box\n14. 检查更新版本\n0. 退出")
            try:
                choice = self.ask("请输入选项")
                if choice == "0":
                    return 0
                if choice in {"1", "2", "3"}:
                    self.create({"1": "reality", "2": "anytls", "3": "hysteria2"}[choice])
                elif choice == "4":
                    self.list_nodes()
                elif choice == "5":
                    self.edit()
                elif choice == "6":
                    self.delete()
                elif choice == "7":
                    self.say(display(self.select()))
                elif choice == "8":
                    self.status()
                elif choice == "9":
                    self.update()
                elif choice == "10":
                    self.backup_restore()
                elif choice == "11":
                    self.say(diagnostics())
                elif choice == "12":
                    self.uninstall()
                elif choice == "13":
                    self.adoption()
                elif choice == "14":
                    self.check_updates()
                else:
                    self.say("无效选项，请输入 0–14。")
            except (PilotError, ValueError, KeyError, TypeError, OSError) as e:
                self.say(f"操作未完成：{e}")
            except KeyboardInterrupt:
                self.say("本次操作已取消；可以继续选择菜单。")
            except EOFError:
                return 0
