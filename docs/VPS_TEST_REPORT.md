# 真实 VPS 验证报告

**最新 RC4 实测见文末“v0.4.0 RC4 实机补充验收”一节。** 以下 v0.1.0 记录保留为历史证据，不代表当前版本完整验收。

日期：2026-10-14。NodePilot 0.1.0；报告不包含服务器地址、登录凭据、节点 ID、认证信息、私钥、SSH 指纹或原有软件配置。原始日志与节点数据仅保留在受保护的私有测试目录。

## 环境与范围

Debian 13、amd64、1 CPU、约 2GiB RAM；已有 x-ui 与其自带 Xray，已有 TCP 443 和 UDP 1443 监听。测试选择其他空闲端口，使用 NodePilot 独立目录/服务，不停止、覆盖或升级原有软件。不改全局防火墙、IPv6、sysctl 或 swap 设置。

本次 TLS 使用明确标记的 30 天自签名测试证书，客户端导入公开 PEM 并保持正常证书验证。没有域名/联系邮箱，未执行 ACME 公网签发；这些结果不能表述为公网可信 CA 证书已经验收。

## 已实际通过

| 项目 | 证据 |
|---|---|
| 安装/TTY | 安装自动菜单、重复安装、源码升级、错误输入循环、stdin 管道交互通过；初次 VPS 执行44项单测通过 |
| 官方内核 | Xray 26.3.27 / sing-box 1.14.3，官方 SHA-256、ELF 架构、报告版本验证通过 |
| 三节点共存 | Reality/AnyTLS TCP独立端口，HY2 UDP与Reality同端口号共存；独立 systemd 单元运行 |
| 导出 URI | 从服务端 URI 解析出客户端连接参数，客户端只接收分享凭据与公开证书，不复制 Reality/TLS 私钥 |
| 外网传输 | 三协议各完成1MiB数据，长度和SHA-256正确；Reality分别使用sing-box与Xray交叉验证 |
| 外网出站 | 三协议均经代理访问 example.com HTTPS，验证远端域名解析与端到端TLS |
| 修改/回滚 | 三协议端口/凭据修改再恢复；人为在原生配置检查后抢占端口，启动失败自动回滚到旧配置/元数据并恢复服务 |
| 备份恢复 | 删除本次测试节点，恢复到空节点集，TLS证书按导入模式恢复；再次外网传输和HTTPS通过 |
| 单节点删除 | 清理时逐个删除本次节点并检查其余节点仍active；未删除或停止其他服务 |
| 本地完整测试 | 新增实际Git暂存blob隐私检查、客户端不含服务端私钥、面板自带内核只读检测等，当前50项 Linux 单测通过 |
| GitHub 入口升级 | 从真实 raw main 安装入口运行 --upgrade，原有三节点保持active，升级后再次通过三协议传输和HTTPS |
| GitHub 入口新安装 | 删除本次测试节点并仅卸载本次创建的 NodePilot，保留私有备份；从真实单条命令重新安装，校验Release包并自动进入菜单 |
| 原服务保护 | 原x-ui主进程PID及原Xray配置SHA-256保持一致；SQLite运行库会因原软件统计而动态变化，未由NodePilot改写 |

## Reality 目标站点观察

最初测试站点通过 TLS 1.3/h2、证书与SNI前置检查，但真实 Reality 连接在两种客户端中失败。替换为另一个通过前置检查的目标后，sing-box 和 Xray 都完成实际传输。具体失败机理没有完整归因，不据此推断所有相同类型站点都不可用。

因此仅通过 TLS 探测或服务端配置检查不足以宣称 Reality 可连。产品不把测试池站点作为生产默认值，安装后仍应从目标客户端验证。测试工具的候选域名仅用于可销毁测试。

## 单核 / 小内存约束测试

独立 systemd 测试进程组，`CPUQuota=100%`、`MemoryMax=192MiB`、`MemorySwapMax=0`，只包含三内核和测试Python工作进程。Go 设置与正式服务一致：`GOMAXPROCS=1`、`GOMEMLIMIT=64MiB`、`GOGC=50`。

为防止预热代码页计费到其他 cgroup，复制已经验证的内核到测试组内的新 inode，比较源/副本 SHA-256 和 ELF 后执行；两个 sing-box 进程共享同一校验副本。没有丢弃机器全局缓存。

- 冷启动负载阶段持续约180秒。
- 每协议8次1MiB传输、最多4并发，总计24MiB；每协议还完成公网HTTPS。
- 测试组峰值 `memory.current = 179793920` 字节，约 **171.5MiB**。
- 三内核 RSS 之和峰值 `172280 KiB`，约168.2MiB；RSS含共享页重复计数，与cgroup物理计费含义不同。
- swap使用峰值0；`memory.events` 的 max、oom、oom_kill均为0；三内核未退出，工作进程正常完成。

第一轮预热计费观察只作诊断；另一次冷启动窗口在传输开始前已结束，仅算空闲启动结果，未计入上述成功负载数据。成功数据来自重新开启窗口后立即进行的真实传输。

**这支持三协议在单核、192MiB进程组预算内完成该短期工作负载，但实际VPS仍是2GiB。** 不包含完整OS/SSH内存、ACME依赖安装或续期，也不是长期/大量连接/持续满速测试，不能替代完整256MiB虚拟机验收。

## 隐私与发布检查

上传使用公共源码允许清单；AGENTS.md、私有SSH会话工具、主机记录、测试配置/日志、证书、数据库、备份、下载内核和客户端URI均不提交。检查实际 Git 暂存 blob / HEAD，不用工作区副本冒充上传内容；额外隐式输入本次会话凭据与私有地址精确扫描，结果不打印秘密。

Release包只包含与Git HEAD逐字节一致的管理器文件；单独验证包SHA-256、源码/附件安装器一致性和绑定URL。提交使用GitHub noreply身份，避免公开个人邮箱。公开源码、CI和附件结果以实际仓库状态为准。

实际已发布 [v0.1.0](https://github.com/km-hl/Node-Pilot/releases/tag/v0.1.0)，仅含 install.sh、管理器tar.gz和SHA-256文件三个附件。首次 [GitHub CI](https://github.com/km-hl/Node-Pilot/actions/runs/38506642885) 已成功完成隐私审查、50项单测、shellcheck、官方内核检查及可复现打包。最终VPS保留管理器与私有备份，当前无测试节点，内核将在选择协议时按需安装。

## 仍待验证

ACME真实签发/临期续期、完整1c/256MiB实机长测、Ubuntu与arm64、Mihomo/v2rayN/Egern GUI导入、长时间高负载与中断/断电恢复。测试结束删除临时节点与负载进程；保留管理器和root私有备份便于后续测试。

## v0.4.0 RC4 实机补充验收（2026-10-16）

本轮使用用户明确授权的 VPS，管理器基线为 `c63106ffd0e834d69e911956dd44c748a3dc1f2d`，payload SHA-256 为 `645d116e5dd1ceceb4c7af3e5b372997e9de36010f9f29481e4bb46e5c2990ae`。报告不包含 VPS/客户端地址、SSH 凭据/指纹、节点凭据、私钥或原服务配置。Mihomo 验收工具的 CPU 兼容修复见下文，管理器 payload 没有改变。

### 环境与原服务边界

Debian 13、amd64、1 CPU、MemTotal 2021232 KiB（约 1.93GiB）、公网 IPv4，无公网 IPv6。原 x-ui 及其 Xray 使用 TCP 443、UDP 1443；Docker daemon 在运行但没有运行容器。原 NodePilot 0.3.0 已安装、节点数为 0，续期 timer 为 enabled + active。

候选源码经过公开允许清单及 SHA-256 校验后放到独立 root 私有目录。测试 Manager 的 state/cores 路径和临时 systemd 单元放到专用子目录及 /run；依赖路径由私有测试驱动注入，**未 Mock 公网 DNS、TLS、协议连接、内核或 systemd 响应**。不升级/卸载原 NodePilot，不接管原 x-ui，不变更全局防火墙、IPv6、sysctl、swap、路由或 Docker 网络。干净新安装、原程序真实升级及其跨重启恢复不在本次成功范围。

AnyTLS/HY2 与 Caddy 的测试证书是明确标记的自签名证书；客户端导入公开测试 PEM，保持正常校验，没有 insecure/skip-cert-verify。没有域名/Cloudflare Token，未执行真实 ACME。

### 真实公网连接与生命周期

| 项目 | 结果与证据范围 |
|---|---|
| 默认第三方 Reality | 从 VPS 实测默认候选、官方 CF 网段、实际 TLS1.3/X25519/h2/可信证书/HTTP 分项；固定已验证 IPv4，保留原始域名和 SNI。IPv6 无路由记录为失败，其他地址继续检测。 |
| 三协议公网数据 | 独立网络客户端分别导入 URI；sing-box 客户端三协议各 2MiB，数据长度和 SHA-256 一致，均可经代理访问 example.com HTTPS。HY2 使用真实公网 UDP 与 salamander。Reality 另以 Xray 客户端完成 2MiB 和 HTTPS。 |
| 错误参数负例 | 三协议错误认证、HY2 错误 obfs 密码、AnyTLS 错误 SNI，共 5 组均拒绝传输；客户端自身已正常启动，正例前后通过，不把测试客户端启动失败当作认证拒绝。 |
| 手动固定与动态模式 | 使用真实网络检测执行手动默认固定 IP，SNI/UUID/shortId/密钥保持，确认前节点不变；明确选择动态域名后服务就绪、DNS 结果独立且 dns_drift 为空。复查不改节点，默认 keep 和恢复原连接参数通过。动态模式不因此获得未来 IP 可用保证；未控制公网域名制造 DNS 漂移。 |
| CF 拒绝 | 实际查询当前官方 IP 段，已知 CF 候选被拒绝。检查未知的二次确认、HTTP 各异常分支仍以单元/本地 TLS 测试为证据，不冒充所有负例的公网复现。 |
| 修改/失败回滚 | 三协议端口/认证修改再恢复；在原生检查后实际抢占监听端口，服务启动失败后自动恢复原修订/参数/服务。故障由受控 socket 注入，服务及回滚是真实 systemd。 |
| 备份恢复 | 本次三节点备份→删除→空实例恢复，证书转 import，刷新 URI/证书后再次各传输 2MiB 并访问 HTTPS；坏证书/原生预检失败和 Token 字节保持另由真实服务 restore_smoke 验证，DNS/ACME 凭据为夹具，未签发。 |
| Caddy 自建目标 | 两个独立本机目标、SNI/证书/端口修改、Xray 启动失败后的聚合配置回滚、单节点删除及证书备份恢复通过。两个目标分别经外部 Xray 客户端完成 2MiB 与 HTTPS。测试证书不代表公网 CA 签发。 |
| 服务/接管边界 | service_smoke、restore_smoke、http_family_smoke 通过；真实系统对原 x-ui 的只读检查拒绝将面板当作独立内核接管。额外创建两个专属临时独立源服务，实际验证启动失败回退、中断记录恢复、接管/归还、原文件/认证保持及 enabled-runtime 策略，全部清理。没有停止或接管原面板。 |
| 资源测试后恢复 | 资源进程组结束后恢复三条临时 systemd 服务；再从外部每协议 1MiB 与 HTTPS 通过，然后逐节点删除并检查删除隔离。 |

本轮各阶段共验证 **45MiB** 数据摘要（包含下节 24MiB 受限组负载），全部是客户端跨公网连接本次 VPS，未替换为 loopback 地址。HTTP 测试数据服务只监听 VPS loopback，经真实代理链路访问；不能据此推断其他端口、其他云安全组或公网 IPv6 都可达。没有 GUI 客户端验收。

### 1 CPU / 256MiB 进程组短测

在此约 2GiB VPS 上，用专属临时 systemd cgroup 限定 `CPUQuota=100%`、`MemoryMax=256M`、`MemorySwapMax=0`、`TasksMax=128`。仅停止本次三节点服务，由工作进程在受限组内执行三内核，使用正式服务相同的 Go 内存参数；原 x-ui/Xray 保持在原组运行。

`resource_probe.py --duration 180 --cold-binaries --lifecycle` 复制已校验内核到新 inode、比较摘要/ELF 后执行，避免把预热代码页记在其他组。组内包括 Python 工作进程、三内核、原生配置检查、内核进程重启及测试证书生成。没有包含 Caddy、完整 OS、SSH、负载数据服务、下载/安装或真实 ACME；服务端备份恢复已在其他阶段完成。

- 完成 180 秒，355 个样本，工作进程正常退出。
- 三协议各 8 次 1MiB、最多 4 并发，共 **24MiB**，全部摘要及 HTTPS 通过。
- `memory.current` 采样峰值及内核 `memory.peak` 均为 **174272512 字节，166.2MiB**。
- 三内核 RSS 之和峰值 **137336 KiB**；RSS 与 cgroup 计费含义不同，不相加作为整机内存。
- swap 峰值 0；memory.events 的 max、oom、oom_kill、oom_group_kill 均为 0。
- 结束后临时组停止，三节点恢复并再次公网传输通过。

该结果支持所测三协议短期负载在 256MiB **进程组**内完成，不能替代真正 1c/256MiB 整机、安装/ACME/升级峰值或长期负载验收，不能承诺所有协议加 Caddy 同时长期稳定。

### 实机发现、自动测试与未执行项目

首次 `tools/mihomo_check.py --download` 的官方未限定 amd64 附件在此 CPU 上退出 1，原始错误包含“can only be run on AMD64 processors with v3 microarchitecture support”。这发生在版本探测阶段，配置解析尚未执行，不能计为通过。核对官方 Release 附件后，工具改选 `mihomo-linux-amd64-v1-<tag>.gz`；保留官方 SHA-256、ELF 和版本检查，不降低供应链校验。arm64 文件名不变，新增回归测试。修复后的官方 Mihomo 1.19.32 下载/验证与三协议 YAML 原生解析在此 VPS 通过。该修复只影响验收工具，不影响已安装节点或管理器包摘要。

实机首次 253 项单元通过；修复工具后，本机与 VPS 的最终完整 **254 项测试均通过，0 failure/error/skip**。VPS 最终执行用时 40.992 秒。Xray 26.3.27、sing-box 1.14.3、Caddy 2.11.7 本次重新从官方来源下载，SHA-256/ELF/版本及 10 项原生配置检查通过；编译、自检、bash 语法、9 个真实 loopback TLS/HTTP 场景通过。远端没有安装 ShellCheck，未为测试改系统依赖；本地与 CI 的 ShellCheck 仍单独验证，不能写成远端也运行。

`http_family_smoke` 的 IPv4/IPv6/双栈是本机高端口监听测试，不代表公网 IPv6 或真实 HTTP-01。API/Zone/boot 的单元夹具、restore_smoke 的凭据夹具、service_smoke 的 Release API 夹具均保留模拟标记。原工具输出的固定“WSL”措辞不改变本轮实际执行地点，资源数字以本节明确说明为准。

本轮未执行：真实 ACME/DNS-01 签发续期及故障 TXT 清理、真实 reboot/断电、干净单命令安装、原管理器升级/卸载、Ubuntu/Debian12/arm64/公网 IPv6、GUI 客户端、真实 Docker 网站反代拓扑，以及 256MiB 整机长测。原服务器已有安装和业务，未用破坏性安装/升级脚本或重启来补这些项目。

### 收尾与发布结论

结束前核对：8 个原关键文件摘要全部一致；原 x-ui/Docker/续期 timer 的运行、启用、加载路径及 MainPID 快照一致，原 Xray PID/可执行文件、NodePilot 0.3.0 current 指向及 0 节点状态保持。无本次 nodepilot 临时单元或监听残留；Caddy/接管/restore 临时状态已清理。主测试状态、源码及下载内核在核对目录归属后删除，生成的私有节点备份/日志压缩包仅保存在 root 专用目录，文件 0600；不公开上传。没有重启主机或操作全局网络策略。

RC4 在此 Debian 13 amd64 IPv4/既有 x-ui 环境下的上述公网、生命周期与短测通过，补齐了此前仅本地验证的一部分。**继续保留 PR #1 Draft，不合并 main、不发布正式 v0.4.0**；仍须完成真实 ACME、重启/升级故障、系统/架构/公网 IPv6/GUI 和 256MiB 整机长期验收。没有据此次有限测试推断整个环境矩阵已通过。

## RC4 程序升级与真实重启专项（2026-10-17）

本节取代前文对“尚无升级/整机 reboot 证据”的当前判断，保留之前各轮的历史边界。用户在本轮明确重新授权原 VPS 进行可销毁测试、安装/升级/卸载、故障注入与多次整机 reboot；不是沿用上一轮共存测试授权。没有授权销毁云实例，也没有重装操作系统。

### 环境、来源与证据边界

- 受测源码：`e4850d1bcca8f8ed483ff6f4f295666b133f9f70`，远端开发分支相同；开始时 PR #1 OPEN/DRAFT，精确提交 CI 成功。管理器 payload SHA-256 仍为 `645d116e5dd1ceceb4c7af3e5b372997e9de36010f9f29481e4bb46e5c2990ae`。
- Debian 13 amd64，1 CPU，MemTotal 2021232 KiB，公网 IPv4；沿用原 x-ui/Xray 与 Docker。**这是同一 VPS 上的全新 NodePilot 安装，不能当作干净 OS、其他系统、arm64 或 256MiB 整机证据。** 全局防火墙、IPv6、路由、DNS、swap、sysctl 均未调整。真实 ACME 域名/Zone/Token 尚未提供。
- 原 NodePilot 0.3.0 为零节点、timer enabled + active。先将其程序、状态、启动器、续期单元保存为 root 私有压缩备份及原路径副本，再腾空精确的 NodePilot 安装路径；没有接管 x-ui/Docker。
- 重新从 GitHub 正式 v0.3.0 Release 下载安装器，核对 API 附件 SHA-256：安装器 `a6517939811feff0dd4e8be7494ea671de8acdec81b999e67c610aeb0ec89992`，payload `8e1fc2bda9a1a0ab10444d134ef18c96b8d0085a6287b2f5132a5f928a0f0671`。实际安装入口继续下载并校验项目包，自动打开 PTY 菜单后正常退出。
- 使用**实际安装的 v0.3.0 管理器**创建三节点，官方来源下载/校验 Xray 与 sing-box；第三方 Reality 为真实 TLS/CF 筛查后选中的目标。AnyTLS/HY2 使用测试自签证书，独立客户端显式导入 PEM，`insecure=0`，从未关闭 TLS 验证。这不属于真实 ACME。
- 升级通过可信源码 `bash install.sh --local --upgrade`，明确确认从旧仓库绑定切换为本地来源。没有调用不存在的 v0.4.0 Release。升级后原 node.json、config.json、证书、URI、节点单元摘要及运行 PID 均相同；schema 所有权迁移为 2，原节点元数据仍可读。

### ACME 验收矩阵

| 项目 | 本轮真实结果 | 现有代码/本地证据与所缺条件 |
|---|---|---|
| Caddy HTTP-01 | 未执行 | 固定正式 LE Directory，TCP80 分族与占用拒绝已有本地证据；需要授权 A/AAAA 域名与公网80。自动续期、两正式证书目标及删除隔离不能由自签结果抵扣。 |
| AnyTLS DNS-01 | 未执行 | 独立凭据/Certbot runner/原生配置已审查；缺测试域名、Zone ID、最小权限 Token。 |
| HY2 DNS-01 | 未执行 | 同上；本轮自签 QUIC 实连不能证明 DNS-01 签发。 |
| Reality 自建目标 DNS-01 | 未执行 | 使用 holder 与同一 Certbot 路径；仍缺真实 CA/Token。 |
| 续期成功、失败保留旧修订 | 真实 CA 未执行 | 现有失败注入证据保持 Mock/systemd 标记；不能写成真实续期通过。 |
| TXT 创建、传播、正常/中断清理 | 未执行 | 需先记录授权 Zone 的原 TXT，再按专属挑战名核对创建与清理；不能清理其他项目记录。 |
| Token 的真实插件日志脱敏 | 未执行 | 独立 0600 文件、环境隔离及脱敏代码/夹具可检查，不能保证所有实际插件输出。 |

当前 Certbot 命令、Caddy issuer 与其证书存储路径均固定正式 LE；没有现成安全的菜单 Staging 开关。下一轮先在隔离的测试配置/源码副本使用 [官方 Staging Directory](https://letsencrypt.org/docs/staging-environment/)（`https://acme-staging-v02.api.letsencrypt.org/directory`），同时显式配置可信 Staging 信任锚及对应存储路径，保留校验；不得只替换 issuer、关闭证书验证或把正式 CA 当无限重试环境。Staging 全部故障与清理通过后，再对授权域名有限正式签发，并单独验证系统信任链、SAN、私钥配对、服务装载和外部 HTTPS。

### 升级与 reboot 矩阵

| 场景 | 重启后的 timer | boot ID 前8位（前 → 后） | 结果 |
|---|---|---|---|
| 正常多节点，enabled + active | enabled + active | `c4393da1` → `b1c192d7` | 新启动周期、三协议监听/原配置/公网 HTTPS 均通过；重复恢复无 pending |
| disabled + inactive | disabled + inactive | `b1c192d7` → `5dfbfbcc` | 新启动周期、三协议监听/原配置/公网 HTTPS 均通过；重复恢复无 pending |
| enabled-runtime + active | disabled + inactive | `5dfbfbcc` → `db1f748b` | 新启动周期、三协议监听/原配置/公网 HTTPS 均通过；重复恢复无 pending |
| disabled + 手动 start | disabled + inactive | `db1f748b` → `1a97804c` | 新启动周期、三协议监听/原配置/公网 HTTPS 均通过；重复恢复无 pending |
| enabled + 手动 stop | enabled + active | `1a97804c` → `bf00c054` | 新启动周期、三协议监听/原配置/公网 HTTPS 均通过；重复恢复无 pending |
| preparing/intent 中断，disabled | disabled + inactive | `bf00c054` → `b0a6962b` | 新启动周期、三协议监听/原配置/公网 HTTPS 均通过；重复恢复无 pending |
| installing/reloaded 中断，enabled-runtime | disabled + inactive | `b0a6962b` → `725e2017` | 新启动周期、三协议监听/原配置/公网 HTTPS 均通过；重复恢复无 pending |
| complete/checked 中断，enabled | enabled + active | `725e2017` → `68c76f3f` | 新启动周期、三协议监听/原配置/公网 HTTPS 均通过；重复恢复无 pending |
| rolling_back/rollback-launcher 再中断，enabled-runtime | disabled + inactive | `68c76f3f` → `345d7789` | 新启动周期、三协议监听/原配置/公网 HTTPS 均通过；重复恢复无 pending |
| 节点 pending 写入后中断，enabled | enabled + active | `345d7789` → `e6e668b5` | 新启动周期、三协议监听/原配置/公网 HTTPS 均通过；重复恢复无 pending |

**10 组真实 RC4 reboot 全部通过**，含 4 组程序事务及 1 组节点 pending。complete 恢复前后程序/current/owner/来源/启动器/续期单元的摘要、inode、mtime 均相同，确认只读最终化。另有 1 次 v0.3 重启单独记录，不计入这 10 组。升级前、升级后、10 次 reboot 后及两批失败检查后，各三协议 1MiB，共 **42MiB** 数据完整性验证，并逐协议 HTTPS 正常校验。

每次均真实执行 `systemctl reboot`，关闭旧 SSH、重新建立连接，核对新 boot ID；恢复后逐节点核对 MainPID 拥有的 TCP/UDP socket、原参数与文件摘要，从本地独立网络重新访问公网节点并验证数据和 HTTPS。没有用 boot ID 夹具、重启单个服务或 WSL 启动代替整机 reboot。事务故障由私有驱动在产品函数**真实写入并 fsync 后 `os._exit`**触发；它是明确的故障注入，证据中保留该标记，CA/网络/时钟没有用 Mock 替代。

| 故障 | 实际方法与证据层级 | 结果 |
|---|---|---|
| 下载失败 | 真实 HTTPS 请求正式仓库内刻意不存在的附件，安装器返回 1 | 未部署；旧 current、节点、单元与 PID 不变 |
| SHA-256 不匹配 | 真实下载 v0.3 附件，设置错误预期摘要；保持校验开启，返回 1 | 安全拒绝；旧状态不变 |
| 安装包损坏/格式错误 | 官方安装器脚本作为刻意无效 payload，摘要正确、tar 原生解包拒绝，返回 1 | 未执行包内代码；旧状态不变 |
| 新管理器加载失败 | 明确无效的候选模块夹具，真实版本/加载探测拒绝 | 旧程序与节点不变；不是发布版自身失败 |
| 候选不兼容旧 schema | 要求 schema=99 的无效候选夹具，真实探测拒绝 | 旧状态不被迁移/覆盖；不是支持 schema 的真实缺陷 |
| 升级异常退出、事务残留、回滚途中再退出 | 上述 4 组 durable journal + os._exit + 真实整机 reboot | 恢复完成且幂等，不强删 pending |
| Xray 更新后启动失败 | 官方同版本重新下载/校验为新修订；真实停止后用 socket 占用测试节点端口，真实 systemd 启动失败 | 自动恢复旧内核修订，全部节点监听健康，原配置/URI 不变 |
| Caddy 更新配置检查失败 | 官方同版本内核下载/校验；刻意注入未知 app 的配置夹具，由真实 Caddy validate 拒绝 | 原内核、配置和目标服务保留；临时自签目标随后删除 |
| 未知/损坏恢复记录、外部 FragmentPath/drop-in | 现有 254 项单元/夹具及此前独立 systemd 证据；本轮未制造此类真实 reboot 故障 | 仅按已有证据层级记录，不能追加为本轮实机组数 |

五项安装/候选拒绝后及两项内核失败后，均再由独立客户端用原分享参数完成三协议公网数据/HTTPS。没有执行未校验的内核，没有用假下载器冒充 HTTPS。两种无效程序和 Caddy 配置是明确的故障夹具，不宣称真实发布附件本身损坏。

### 命令与复现边界

以下命令只适用于明确授权的专用测试安装。先制作私有备份，保存 `sha256sum`、`systemctl show`、`ss -lntup`、`/proc/sys/kernel/random/boot_id`；禁止对未授权服务器照搬。私有 SSH 驱动只编排现有 `tty_command`、打包、Manager/deploy 和 `client_probe.py`，不进入产品安装包，也不包含凭据到公开仓库。

```bash
# 先校验 GitHub 正式 Release 安装器摘要，再执行；实际自动进入菜单。
bash /path/to/verified-v0.3.0/install.sh
# 可信候选源码的真实升级入口；来源变更明确输入 y。
bash install.sh --local --upgrade
systemctl show nodepilot-renew.timer -p UnitFileState -p ActiveState
# 每项策略独立设置，分别保存 /etc、/run 启用链接；绝非全设 enabled。
systemctl stop nodepilot-renew.timer
systemctl disable nodepilot-renew.timer
systemctl disable --runtime nodepilot-renew.timer
# enabled 场景：enable + start；enabled-runtime：enable --runtime + start；
# disabled+active：仅 start；enabled+inactive：仅 enable。
cat /proc/sys/kernel/random/boot_id
systemctl reboot
# 新 SSH 会话再次读取 boot ID，用可信 RC4 恢复并再次执行验证幂等性。
python3 tools/client_probe.py --private-dir /path/to/private-client-artifacts --https-check
```

合法程序中断复现：先用现有 `tools/package.py` 为可信候选副本计算真实 payload 摘要；私有驱动在 `deploy.checkpoint` 原函数成功返回后的 reloaded/checked/rollback-launcher 阶段终止进程，初始 intent 则在原 atomic_json 成功返回后终止（程序场景预期退出 77）。节点 pending 在原 `manager.atomic_json(pending.json, …)` 成功返回后终止（预期退出 78）。保存日志后 reboot，再在 `Manager(migrate_owner=False).locked(migrate_owner=False)` 内调用 `recover_program`、`recover_core`、`recover`，重复恢复并核对无 pending/节点漂移。除候选副本的测试注释和特意无效的加载夹具外，不改已审核管理器，绝不删除 pending 绕过恢复。

### 本地检查、收尾与发布判断

本轮本地 Debian WSL 的现有 **254 项单元/夹具测试通过**（6.902 秒）。Windows 原生尝试因缺 Linux fcntl 等条件出现 4 errors/134 skipped，不计为通过；随后在受支持 Linux 重跑。测试驱动初次使用 `operator.py` 与标准库同名、首次来源确认误发 `yes` 均已纠正，安装器在未获 `y` 确认时安全拒绝；没有把这些失败计为升级成功。一次提前排队的重启发生在旧版，单独保留为 v0.3 实际 reboot 证据，不混入 RC4 组数。

VPS 最终 **254 项测试全部通过，38.529 秒，无失败/跳过**。本轮内核版本为 Xray 26.3.27、sing-box 1.14.3、Caddy 2.11.7。服务器未安装 Certbot、无 global IPv6 地址；未安装 ACME 依赖、未操作 Cloudflare、未请求任何 CA 证书。远端仍没有执行 ShellCheck，不能由 CI 或本地结果冒充。

测试结束通过 NodePilot 自身卸载事务清理本次安装，保留私有备份/审计后将原路径副本原样移回。原 **103 个记录文件 SHA-256 全部一致**，原 NodePilot **v0.3.0 / 零节点**、timer **enabled + active** 恢复，x-ui/Docker active，临时节点/Caddy/payload 单元及监听清理。外部服务 PID 因已授权整机 reboot 改变，不能声称与本轮开始相同；文件完整性与运行状态分别检查。原始 journal（含敏感数据）和备份仅留 root 私有目录；本地忽略目录仅取回客户端必要参数、公开测试 PEM 与状态摘要/日志，不公开上传。验收 receipt 压缩包摘要 `10be8436026415f036f750950628118bb1eda7fe1357ffb1d2a723972134ecec`（29 个文件），原始文件不进入 Git。

测试驱动首次 intent 停止点未命中时自动停止；检查无 pending/节点损坏后改为在真实初始 atomic_json 返回后退出，再重跑该场景。此为私有注入器修正，没有修改产品恢复逻辑。SSH 重连期间的短暂 banner 超时在确认新 boot ID 后恢复，不计为节点错误。

管理器行为没有新增修改，本轮不增加功能、不改变 CA 默认行为、不更新 CHANGELOG。未确认新的未修复 P0/P1；真实 ACME 等未验收风险仍存在，不能据此宣称没有 P0/P1。真实升级/重启结论限于上表测试场景；正式 Release 下载升级、真实 CA、其他系统/架构/公网 IPv6、GUI、Docker 网站反代、256MiB 整机长期及安装/ACME 峰值仍未验证。**PR #1 继续 Draft，不解除草稿，不合并、不创建 RC5、不发布正式 v0.4.0。**
