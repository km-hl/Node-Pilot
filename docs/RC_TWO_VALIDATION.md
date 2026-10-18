# v0.4.0 RC2 验证和发布判断

日期：2026-10-15。在既有 `codex/v0.4.0-stability` 分支增量修改，基线 `bc253aa743ae1edc3657e46dbe8bf0977ac424e9`；main 仍为 v0.3.0 / `548e84202589dc0be5898a02552541a0065a6de5`。开始前已 fetch，PR #1 OPEN / DRAFT，无新提交，两项 CI 均 SUCCESS。修改前 Debian Linux 基线 121 项通过。

## 本轮修复

| 范围 | 行为与边界 |
|---|---|
| HTTP-01 | DNS 全部 A/AAAA 必须属于探测到的本机公网地址；只检查发布的地址族，独立 IPv6-only socket 检查，不修改 IPv6/sysctl。Caddy 使用 `tcp4/0.0.0.0:80` / `tcp6/[::]:80`，启动后检查 MainPID 的相应地址族 socket 及本机分族连接。Certbot standalone 单族显式绑定；双栈无法可靠保证，检查两族后明确拒绝，提示 DNS-01 / Caddy。 |
| Token 创建 | 格式、Zone ID、传播时间先检查；0600 临时文件和进程身份标记；验证 Zone 读取及域名归属。用户确认、证书及原生检查、服务监听成功后才登记正式凭据。取消、参数/Zone/签发/加载失败均清理未提交 Token；崩溃后先恢复事务，再清理已退出进程的临时凭据，不删除其他存活会话的 Token。 |
| Token 共享/删除 | 可输入正式 cf-ID 复用。菜单 10 → 7 只读审计；再次确认且重新扫描无引用才删除。当前/旧修订、备份、接管、节点/内核/卸载/删除事务均保留引用；损坏 JSON、未知 schema、不安全链接拒绝清理。删除节点会生成备份，通常仍有引用，不能把“当前没有节点”理解为可删除 Token。 |
| DNS 失败 | DNS runner 在日志记录形成前对消息、异常栈及输出脱敏；隔离 SDK 环境和 certbot cli.ini。超时/中断先给插件 20 秒清理机会，再终止。API 故障、强杀和断电仍可能留下 TXT，错误提示要求核对 `_acme-challenge` 和私有日志；不擅自删除无法确认归属的 DNS 记录。 |
| 证书/备份 | 复用有效证书、临期续期、强制操作提示 CA 频率限制。新 PEM 必须有效、匹配 SNI/私钥，通过内核及加载检查才发布；失败恢复旧修订并记录续期异常。PEM/私钥和节点元数据进入私有备份；DNS Token、certbot 账户不进入便携备份。跨机恢复统一导入模式并移除 DNS 引用，重新绑定后才能自动续期。 |
| 固定 Reality IP | 菜单 8 → 5 手动复查固定 IP 的 TLS1.3、经典 X25519、h2、可信证书和 CF 条件，显示 UTC 时间、当前 DNS 地址及差异。检测记录独立于节点修订，目标变化后旧结果失效。失败提示菜单 5 手动重选，绝不后台更换 SNI、target 或认证。 |
| OpenSSL | 接受明确 `Server Temp Key` / `Peer Temp Key` 的经典 `X25519`；仅提供 `-groups X25519`，未知、相似名称和混合组返回未验证，无不校验证书回退。没有完成四发行版的实际输出矩阵，不能拿模拟文本冒充兼容实测。 |
| 程序升级 | 安装器以 Python 隔离入口显式选择已校验 payload，忽略 cwd 同名包。新程序预检后保存升级意图、旧版本和私有备份；恢复重新逐文件 SHA-256 / self-check，完成安装而非调用旧安装器。普通错误恢复旧程序、launcher、单元、timer 状态和所有权标记；掉电保留日志，新安装器/新管理器可幂等续做。 |
| 服务/删除/回滚 | 区分正在重启、异常重启、缺失监听、本机通过和公网未验证；严格保留外部单元拒绝规则。Caddy 修改、内核更新和中断恢复使用原配置字节，避免改变旧监听语义。删除进入最终清理时使用节点目录外的日志，部分 rmtree 后仍能幂等完成，不影响其他节点。卸载继续区分服务阶段回滚与 finalizing 续做，不声称原子。 |
| 低内存 | 显示 MemTotal / MemAvailable，创建前提示低内存优先单协议。内核按需安装；256MiB 三协议不作稳定保证。所有测试限额只作用于隔离 cgroup，不改全局 swap/网络。 |

## 实际验证与证据范围

最终本地单元/配置/安全/事务测试 148 项通过，包含本轮新增 27 项测试。证书测试使用真实 OpenSSL PEM：临期、已过期、正常更新、加载失败恢复、损坏 PEM、私钥/SNI 不匹配、跨机恢复去除 DNS 引用；CA 返回使用故障注入，不能称为真实签发。程序在切换链接、写 launcher、写 timer 时注入进程终止，重复恢复通过；篡改候选文件拒绝执行。删除中途丢失内部 pending 文件后，外部事务日志恢复通过。

已执行：

```bash
python3 -m unittest discover -s tests -v
bash -n install.sh
shellcheck install.sh
python3 nodepilot self-check
python3 tools/native_check.py --download
python3 tools/mihomo_check.py --download
python3 tools/http_family_smoke.py
python3 tools/install_smoke.py
python3 tools/install_smoke.py --legacy-source <可信-v0.3.0-Git-源码目录>
```

- Debian WSL / root / systemd：干净安装自动 TTY、重复安装保留 sentinel、候选自身升级、v0.3.0 Git 标签真实安装器到候选版升级、cwd 同名包干扰隔离、无效菜单循环、stdin 管道 TTY、完整卸载通过；全部临时全局路径/单元清理。WSL 不冒充全新云 VPS/完整小内存机器。
- 官方 SHA-256、ELF、版本校验：Xray 26.3.27、sing-box 1.14.3、Caddy 2.11.7。8 项原生配置检查通过，Caddy HTTP 分族配置可解析；DNS-01 用有效测试 PEM，未访问真实 CF/CA。
- 官方 Mihomo v1.19.32：三协议导出、IPv6、特殊字符经原生 parser；未冒充 Mihomo 实际连接/GUI 导入。此前三协议 sing-box 外部传输证据见 RC1 报告，不冒充本轮重跑。
- 真实 Caddy 进程：IPv4、IPv6、双栈高端口 HTTP 响应与 PID/socket 归属通过。这只是本机监听，不能证明公网 TCP80、云安全组或 ACME。
- 授权 Debian 13 amd64 VPS：最终 148 项通过。可信已安装 v0.3.0 程序创建三种临时节点，RC2 保持 metadata、URI、原生配置和 MainPID 监听；修改 AnyTLS 端口再恢复通过，旧管理器拒绝 schema=2；临时旧节点清理完成。
- 同一 VPS 的 RC2 三种独立服务及 TCP/UDP 监听、本机 Reality 代理 HTTPS 通过。注入签发网络失败保留旧证书/修订/监听；注入卸载第二服务失败后全部恢复；最终清理中断后续做通过。临时服务、证书、客户端测试资料和 payload 均清理，生产 NodePilot 保持 v0.3.0，x-ui active 且配置 SHA-256 与测试前一致。

## RC2 256MiB 复测

VPS 实际约 2GiB/1 CPU；隔离组 `MemoryMax=256M` / `MemorySwapMax=0` / `CPUQuota=100%`，每组采样 18 秒。冷复制/校验、测试证书生成、配置检查、重启、每协议 2MiB 加密传输及 HTTPS；Reality 含 Caddy，三协议组额外完成官方 sing-box 候选下载/校验/更新。流量客户端在同主机组外，仍不是完整 256MiB 整机。

| 组 | kernel memory peak MiB | core RSS MiB | swap |
|---|---:|---:|---:|
| Reality + Caddy | 120.37 | 78.80 | 0 |
| AnyTLS | 116.15 | 61.01 | 0 |
| HY2 | 113.51 | 62.66 | 0 |
| 三协议 + Caddy | 256.04 | 203.87 | 0 |

三协议再次触及限制，不能把受回收约束后的峰值当成自然需求。真实 CA/DNS 传播/签发的峰值尚未测得；这里的证书操作是 OpenSSL 测试证书，不能冒充真实 ACME。未重测 RC1 的 512/1024MiB 组。

## 审查和恢复边界

在最终完整 CI 前，按安装/下载执行来源、所有权、Token 引用、证书路径、原子替换、服务归属、升级/删除/卸载各中断阶段及老数据兼容性逐项审查。修复了 cwd 模块混入、旧 Caddy 重生成回滚差异、删除内部日志被一并删除、升级失败标记/timer 状态不一致等问题，并补回归。审查不等于发行版/公网/真实 DNS 验收。

RC2 所有权标记为 `schema=2`，读取兼容旧 `schema=1` 节点和安装，节点元数据仍为 schema 1。迁移后旧 v0.3/RC1 管理器/安装器会拒绝该根目录，不能用 main 的旧入口强行修复。程序 intent 持久化后续做安装；节点/内核事务恢复旧修订；删除/卸载 finalizing 仅继续清理。开机两分钟 timer 会调用当前管理器恢复再检查续期，但**尚未做真实断电/整机重启验收**。如果中断发生在新程序 current 切换前，旧管理器无法理解新日志，应使用可信 RC2 或更新安装器 `bash install.sh --local --upgrade` 修复。不得借用旧安装器清理新事务。

旧 HTTP-01 Caddy 监听元数据可能需要手工重新应用域名配置才能迁移到明确分族监听；遇到旧 IPv6 wildcard 无法证明 IPv4 归属时拒绝预检，不停第三方服务，原节点继续运行。节点及内核失败恢复原 Caddy 配置。云防火墙本机无法完整证明；发布的所有地址均需外部验证。

## 官方规范核对

- [Certbot standalone](https://eff-certbot.readthedocs.io/en/stable/using.html#standalone)：默认 IPv6 后 IPv4，至少一个绑定成功即可继续；RC2 不把它当双栈成功证据。
- [Caddy 网络地址](https://caddyserver.com/docs/conventions#network-addresses)：明确支持 `tcp4` / `tcp6`，后缀限定地址族。
- [certbot DNS Cloudflare](https://certbot-dns-cloudflare.readthedocs.io/en/stable/)：插件负责 TXT 创建/清理；DNS:Edit 限定所需 Zone，默认传播 10 秒。NodePilot 默认 60 秒可设 10–600 秒，Zone 归属读取额外需所需 Zone 的 Zone:Read。格式/只读 Zone 检查不能证明 DNS:Edit，实际写权限仍待挑战验证。
- [ACME DNS-01](https://www.rfc-editor.org/rfc/rfc8555#section-8.4)：验证 `_acme-challenge.<申请域名>`；交给官方插件，不自行拼错父域，也不使用 Global API Key。

## 未完成验收与发布建议

**不推荐现在合并到 main；不满足 v0.4.0 稳定发布条件。保持 PR #1 草稿，不创建 Release。**

1. 没有授权域名与 CF Token：真实 TXT 创建/传播/失败清理、Token 失效/Zone 权限变更、Staging/正式 CA、自动续期、真实插件日志脱敏与 Caddy 加载证书均未验收。没有要求把 Token 上传 GitHub。获得授权后应在隔离环境先以 Let's Encrypt Staging 签发；测试链需显式信任，不能关闭校验，也不能作为默认生产证书。
2. 无公网 IPv6 测试路由：IPv6-only / 双栈真实 HTTP-01 与云安全组验证未完成。本机 socket/Caddy 测试只是局部证据。Certbot 双栈当前为安全拒绝，不声称支持完整双栈 standalone。
3. Debian 12、Ubuntu 22.04/24.04、arm64 真实系统安装/升级/输出矩阵，真实重启恢复仍缺证据。CI Ubuntu 24.04 amd64 只能证明该 runner 的自动化检查。
4. v2rayN/Egern GUI 与完整 Mihomo GUI/联通矩阵未验证；不能由分享 URI / YAML parser 推断 GUI 支持。
5. 没有完整 1C/256MiB 整机、真实 ACME 峰值及长期业务测试。历史 12 组 cgroup 短测见 RC1；本轮受限复测单列，仍不能把 OS/SSH/公网客户端开销排除后宣称整机保证。

## 候选产物

仅生成本地可复现包及 SHA-256，安装器仍指向尚未发布的未来 v0.4.0 附件；main 稳定入口保持 v0.3.0。

- 包 `nodepilot-0.4.0.tar.gz`：`ba7a7c40340c6cf4dd4beaa11c728913019e8d6c6d479242e55a04f1ee5b0917`。
- installer：`f5b8d9cc1dc96b3845b9836a05bd212c52512a5f4896a1f2447bf53bfdff01a5`。
- 校验文件：`6889a5468e6cd44ddde0740efc7af6649adbe6acf1a6654c11054c021b330404`。
- Windows/Linux 重新打包逐字节一致，安装器固定绑定包 SHA；提交后另以 `release_audit.py` 对照 Git HEAD 的全部运行时文件，不上传 dist 或测试/运行数据。
- 待上传 Git blob 75 个公开源文件白名单及已知会话敏感值检查通过。完整 CI 新增 self-check、官方 Mihomo 及真实本机分族 Caddy 检查；具体提交/CI 状态以 [PR #1](https://github.com/km-hl/Node-Pilot/pull/1) 为准。
