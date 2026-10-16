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
