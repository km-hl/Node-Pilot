# 真正 VPS 上的第一阶段验收

**当前支持分级和发布门禁见 [最终发布审查](RELEASE_AUDIT.md)，GUI 操作见 [客户端验收清单](CLIENT_ACCEPTANCE.md)。** 本页是完整扩展验收计划，不要求把所有未验证平台变成本次正式支持。v0.4.0 已验收声明收窄为 Debian13/amd64/IPv4/约2GiB；下面的8个系统架构组合、256MiB整机和公网IPv6等是扩大支持范围前的补测项目，不能写成已通过。

本清单并非全部通过；最新授权 RC4 Debian 13 amd64 IPv4 公网/生命周期/进程组短测见 [VPS 报告文末](VPS_TEST_REPORT.md)，[v0.2.0 报告](CADDY_VALIDATION.md) 保留历史证据。本轮同机全新 NodePilot v0.3.0 正式安装→RC4 升级、10 组真实 reboot/恢复及失败保留已通过；干净 OS、正式 v0.4.0 Release 下载升级、完整系统/arm64/公网 IPv6/GUI、自然临期长期行为及完整 256MiB 整机长测仍待执行。真实 ACME 的当前通过范围见文末2026-10-18专项；不要将配置与监听检查标为外网成功。

1. 准备 1c/256MiB 实机，记录 `free -m`、架构、系统、已有服务和防火墙；另准备有 Caddy / Xray / sing-box 的测试机检查共存。
2. 从真实发布 URL 执行单条安装命令。确认安装器校验项目包、选择节点时校验官方稳定内核并自动进入菜单。测试普通 SSH、`curl ... | bash` 和窄终端。
3. 默认创建自有域名 Reality，Caddy HTTP-01 自动申请、等待本机目标证书/TLS 就绪，验证 Xray PROXY v1 和真实 Vision 客户端连接；再测试无域名时从 VPS 实测目标。明确 CF 或 TLS 不合格站点拒绝；301/302/403/404 的有效响应头独立检查 CF 并提示网页质量，不当作 TLS 失败；确认分享未含私钥。
4. 创建 AnyTLS TCP 8443。先检查域名全部 A/AAAA，放行 HTTP-01 TCP 80，用 ACME 实际签发并验证链；再测证书导入、SNI 不匹配拒绝和测试自签名手动信任。
5. 创建 HY2 UDP 443，按需使用 salamander。检查 ss、主机防火墙和云安全组；从外部客户端真实 QUIC 连接，测试密码和混淆错误。
6. 分别在目标版本 Mihomo/Clash Meta、sing-box、v2rayN、Egern 测导入与连接；记录版本和不支持项，不把某一个客户端成功推广成所有兼容。
7. 修改节点端口/SNI/密码，核对当前 config、node.json 和 URI 同步；占用端口和坏证书必须失败且旧节点仍可连接。核对 TCP/UDP 同端口共存。
8. 删除一个节点，确认其服务停止，其他两个仍可连接；确认其他软件服务、配置和证书未变化。
9. 修改时使用 SIGKILL 或重启测试恢复 pending；项目包/内核下载中断及 SHA-256 不匹配必须中止且没有执行坏二进制。更新所有内核及脚本后节点仍存在并可连接。
10. 测 ACME 临期续期和续期端口被占用/DNS 错误时保留旧证书；检查 nodepilot-renew timer 日志，不对公网生产账户反复签发触发 CA 限流。
11. 测可信备份恢复到空节点，SNI/密码和 URI 一致；确认导入模式证书不会误报 ACME 自动续期已经恢复。
12. 三节点空闲、单连接、并发连接、连续 QUIC 传输、证书续期分别记录三进程 RSS、cgroup MemoryCurrent、OS 剩余内存、CPU、丢包和 OOM 日志。至少连续运行数小时，确认 256MiB 实机无 OOM；未达标前不声称该规格生产可用。
13. 卸载需二次确认，只停止/删除精确属于 NodePilot 的文件与单元，保留凭据备份；再次确认 Caddy/Xray/sing-box 服务未改变。

记录格式：系统/规格、测试日期、内核与客户端版本、命令、真实输出、结果、失败原因。凭据/私钥/ACME 账户不得贴进公开 issue 或测试报告。

## RC4 最终验收执行顺序（部分已通过）

本节是完整计划；单机部分通过项以 VPS 报告为准，下面矩阵尚未整体通过。仅使用明确授权的测试范围、域名和最小权限 Token；既有业务必须保留，其他项目凭据不在授权范围。已有 `tools/vps_smoke.py`、`client_probe.py`、`resource_probe.py` 等工具继续复用，不建立第二套验收框架。

### 环境矩阵与记录

| 维度 | 必须记录的实机组合 | 当前 RC4 状态 |
|---|---|---|
| 系统/架构 | Debian 12、13，Ubuntu 22.04、24.04；每系统 amd64/arm64，8 个基础组合 | Debian 13 amd64 同机全新 NodePilot 安装/升级及 10 组 reboot 已通过；干净 OS/其他组合待验收 |
| 公网地址族 | 基础组合中覆盖 IPv4-only、IPv6-only、双栈；异常 IPv6 路由另测 | IPv4 三协议已实连；公网 IPv6-only/双栈待验收 |
| 共存 | 独立 Xray/sing-box、3x-ui、系统 Caddy/Nginx TCP80/443、Docker 网站/反代与端口映射 | 原 x-ui/Xray 保持、临时独立服务接管归还通过；Docker 无运行容器，网站/反代完整拓扑待验收 |
| 内存 | 真实 1c/256MiB（记录 swap），三协议分别及同时运行、自建 Caddy、安装/升级/ACME 峰值 | 2GiB 实机 256MiB cgroup 短测通过；完整整机/长期/ACME 仍待验收 |
| 客户端 | 目标版本 Mihomo、sing-box、v2rayN、Egern，逐协议导入、验证证书、外网传输 | sing-box 三协议、Xray Reality 公网通过；Mihomo 原生解析，GUI 仍待验收 |

每个组合记录 commit、安装器和 payload SHA-256、OS/架构、内核/客户端版本、地址族、时间、阶段、返回码及脱敏结果。私有 URI/密钥/Token、原始日志留本机，公开报告只保存审核后的摘要。

### 可执行入口与三协议连接

正式 v0.4.0 Release 附件尚未发布，当前使用审核过的候选源码运行 `bash install.sh --local`；另测试 `--local --upgrade`。默认在线入口的成功验收须等受审查附件可用，不能用 curl 替身标为下载成功。确认 SSH TTY、窄屏、重复安装及管道入口，并检查已有服务未改变。

在新建的授权测试机，记录安装前服务/文件/防火墙状态后，可使用现有工具创建测试节点：

```bash
python3 tools/vps_smoke.py setup --private-dir /root/nodepilot-acceptance
python3 tools/vps_smoke.py status --private-dir /root/nodepilot-acceptance
python3 tools/vps_smoke.py export --private-dir /root/nodepilot-acceptance
python3 tools/vps_smoke.py payload --private-dir /root/nodepilot-acceptance
```

`setup` 需真实 SSH_CONNECTION，按正式选站流程筛查并固定 IP；无合格目标安全中止。工具的 AnyTLS/HY2 是测试自签名，**不能验收真实 ACME**。`payload` 保持前台，在另一会话完成后停止；它只监听 loopback。通过已授权 SSH 安全取回 clients.json、payload.json 及公开测试 PEM 到独立客户端，目录权限 0700，禁止公开上传；服务端 Reality/TLS 私钥不取回。在独立网络的 Linux 客户端使用已校验内核：

```bash
python3 tools/client_probe.py --private-dir /path/to/private-client-artifacts --requests 2 --parallel 2 --https-check
python3 tools/client_probe.py --private-dir /path/to/private-client-artifacts --protocol reality --reality-core xray --requests 2
```

核对真实 VPS 公网地址、三协议数据长度与 SHA-256、HTTPS 正常校验；跨公网测试才记录公网成功。字段 external_encrypted_transfer 本身不能证明测试跨公网。同机/WSL/地址替换为 loopback 必须标为本地。GUI 客户端另导入/实连；错误密码、错误 obfs、错误 SNI 应失败，主机防火墙/云安全组分开检查，UDP 不能由 TCP/ss 推断。

运行 `python3 tools/vps_smoke.py lifecycle --private-dir /root/nodepilot-acceptance`，再从外部重连，检查修改/回滚/删除隔离。菜单另外验收手动域名默认固定、手动 IP 不变、SNI 与原域名不同、多个 A/AAAA/部分不可达、CF 未知两次确认及取消无改动、动态域名风险确认和独立 DNS 状态、keep 认证参数不变；对照 node.json、原生配置、URI，受控改变原域名 DNS 后仅写诊断、不自动改 target。

### 证书、重启、升级与归还

- 授权测试 Zone 先用隔离且审核过的 Staging 配置验收 TXT 创建、传播、成功/失败清理，再正式签发一次。当前程序固定正式 CA；不要误将现有菜单当作 Staging 开关。验证 AnyTLS/HY2/Caddy DNS-01 新证书、私钥配对、SNI、真实客户端连接、自动续期；注入 Zone/Token/网络错误及中断，旧证书/修订/服务须保留，核对 TXT 残留与真实插件日志脱敏。
- 记录 timer 的 UnitFileState、ActiveState、/etc 与 /run 启用链接。分别测试 disabled+active、enabled+inactive、enabled-runtime 及正常 enabled；真实重启后按开机策略恢复，不重放上一启动的 runtime/手动运行状态。测试升级 intent/current/checked 和 rolling_back 阶段异常/断网/摘要错误/重启，用可信 RC4 恢复；再运行一次恢复确认无重复外部操作。未知/损坏证据保留并拒绝。
- 对正常/损坏证书、旧 HTTP-01/DNS-01 备份做全包预检；凭据不迁移，自动证书恢复为 import 且显示续期关闭；接管备份拒绝便携恢复。临时外部同名单元、FragmentPath 和 drop-in 不匹配必须在操作前拒绝。
- 独立服务接管必须两次确认；3x-ui/包装器拒绝。验收归还及失败恢复、连续恢复不重复改变源服务；确认源文件哈希、启用/运行策略。最后运行 cleanup（仅测试节点），再在菜单二次确认卸载，核对原服务/网站/Docker 网络及证书仍正常，保留私有备份。工具不会代验收所有生产共存拓扑。

### 低内存与最终门槛

按上述 1c/256MiB 真机测安装、下载/解包、三内核启动、证书签发/续期、程序/内核升级与连续数据传输，记录整机 MemAvailable、CPU、RSS、memory.current/peak、swap 和 OOM 日志。已有 resource_matrix 仅提供 18–60 秒 cgroup 对照，整机长测至少数小时并另行记录；超过预算或 OOM 就不能宣称该规格支持三协议同时部署。不要在验收时擅自调整全局 sysctl、IPv6、swap 或防火墙。

所有必验项目均通过、失败恢复可复现、私密产物审计通过后再评估正式 v0.4.0。当前允许冻结候选进入测试，PR #1 保持 Draft，不合并 main、不创建 RC5 或正式 Release。

## 2026-10-17 专项进度与剩余条件（历史，见下一节更新）

用户重新授权原 VPS 后，v0.3 正式 Release 安装→可信 RC4 本地升级、10 组真实 reboot（五种 timer 状态、intent/reloaded/complete/回滚中断、节点 pending）、三项真实安装器拒绝、两项无效候选夹具、官方内核启动失败回滚/Caddy 原生拒绝已通过。每次重启均有新 boot ID、原节点摘要与公网三协议重连；本轮 42MiB 数据、254 项实机测试通过，原安装/103 个文件摘要及续期策略恢复。详见 [完整矩阵与复现入口](VPS_TEST_REPORT.md#rc4-程序升级与真实重启专项2026-10-17)。不能据此把未知/损坏事务的全部夹具计为真实重启测试，也不代表干净 OS 或断电测试。

真实 ACME 仍为发布阻断项：需授权测试域名、Cloudflare Zone ID、目标 Zone 的 DNS:Edit/Zone:Read Token（仅私有文件路径），及 TXT 创建/清理、Staging 和有限正式签发授权。服务器当前无 Certbot/global IPv6；实际插件依赖与公网 IPv6 另验。现有代码固定正式 CA，Staging 测试必须采用隔离且审核过的官方配置、独立状态和显式测试信任锚，不关闭 TLS 校验，不把测试证书当作正式信任通过。AnyTLS/HY2/Caddy DNS-01、Caddy HTTP-01、真实续期/失败清理/Token 日志仍逐项未执行；上文对应步骤保留为待做清单。

当前不解除 PR #1 Draft，不合并 main，不创建 RC5 或正式 Release；等待 ACME 与其余平台/低内存验收后再次评审。

## 2026-10-18 ACME 专项进度

用户提供并明确授权专用域名/Zone/Token后，Debian13 amd64 IPv4 的 AnyTLS/HY2/Reality DNS-01、双Caddy HTTP-01 均完成 Staging 后有限正式签发；两阶段各3次手动强制续期通过，Caddy在显式加速的Staging窗口完成真实自动续期后恢复配置。九组受控故障保留旧证书/修订/服务，真实SIGTERM挑战清理、无效Token/Zone/插件、子进程断网、坏证书/密钥、实签后启动失败和错误TXT均有分层记录。完整 [ACME矩阵、命令与边界](VPS_TEST_REPORT.md#rc4-真实-acme-专项2026-10-18) 取代上一节缺凭据/未签发的当前结论。

正式限定8张，两个环境首次/续期/删除后累计28MiB公网数据及HTTPS验证；各254项本地/实机测试通过，103个原记录文件、服务、监听及PID保持。7条本轮A记录及TXT清理，服务器Token副本/测试状态移除；新发行版Certbot/插件保留，发行版timer关闭，原NodePilot timer不变。Token精确扫描与凭据引用检查通过，原始凭据与私钥不公开。

仍待验收：公网IPv6/双栈ACME、默认自然临期长期行为、真实长期传播超时（本轮错误TXT为注入）、其他系统/arm64/GUI、干净OS/正式v0.4.0 Release附件下载、Docker网站完整拓扑和256MiB整机长期/ACME峰值。默认产品入口仍固定正式CA，没有增加Staging功能；Staging副本/专用信任锚不能用于普通生产。PR #1按要求保持Draft，不合并、不发布，剩余项目须按发布范围明确处置。
