# NodePilot

当前为 **v0.4.0 RC4 候选**，PR 保持草稿；[RC4 验证报告](docs/RC_FOUR_VALIDATION.md) 记录安装器活动事务保护、四版本恢复、真实 systemd 验证及发布阻塞项。[RC3 报告](docs/RC_THREE_VALIDATION.md) 和 [RC2 报告](docs/RC_TWO_VALIDATION.md) 保留此前证据。不建议现在合并或发布稳定版。

轻量 VPS 节点搭建与管理工具，当前源码 `0.4.0` 候选。支持 **VLESS + TCP + REALITY + Vision**、**AnyTLS + TLS**、**Hysteria2 + TLS + QUIC**。Bash 引导安装，Python 标准库终端菜单，官方预编译 Xray / sing-box / Caddy，无 Web 管理后台。

**当前状态：三协议已在 Debian 13 真实 VPS 上通过外网数据传输、HTTPS、生命周期及受限资源短测。** TLS 使用明确标记的测试自签名证书并在客户端导入信任，未关闭验证；ACME 实际签发、完整 256MiB 实机长测及 GUI 客户端矩阵仍待验证。详见 [真实 VPS 测试报告](docs/VPS_TEST_REPORT.md)、[本地记录](docs/VALIDATION.md) 和 [验收清单](docs/VPS_ACCEPTANCE.md)。

仓库：[km-hl/Node-Pilot](https://github.com/km-hl/Node-Pilot)，当前版本入口 [v0.3.0](https://github.com/km-hl/Node-Pilot/releases/tag/v0.3.0)。Caddy 与选站实测见 [v0.2.0 验证记录](docs/CADDY_VALIDATION.md)，版本选择与接管实测见 [v0.3.0 验证记录](docs/MANAGEMENT_VALIDATION.md)。

v0.4.0 为候选交付，尚未发布稳定 Release；公开 main 安装命令继续提供 v0.3.0。新增 DNS-01、证书复用、卸载恢复、Reality 组检测、监听诊断和 Mihomo YAML，详见 [变更记录](CHANGELOG.md)、[审查计划](docs/VERSION_FOUR_PLAN.md) 和 [v0.4.0 验证记录](docs/VERSION_FOUR_VALIDATION.md)。候选源码可使用下方 `--local` 安装，默认不绑定在线更新源；Release 模式的 v0.4.0 下载入口只在正式发布附件后生效。

```bash
bash <(curl -fsSL https://raw.githubusercontent.com/km-hl/Node-Pilot/main/install.sh)
```

安装器绑定固定版本 Release 附件 URL 和 SHA-256，不从未校验包执行管理器。

## 本地源码安装

将源码放到支持的 VPS，进入项目目录，以 root 运行：

```bash
bash install.sh --local
```

本地模式只使用你明确信任的源码打包和校验，安装来源记录为 `local-source`，不会继承脚本模板的 Release URL，也不从工作目录或 Git remote 推断授权。Fork 或自行修改的源码同样默认没有在线更新绑定。

安装完成立即打开菜单。以后只需：

```bash
nodepilot
```

支持 Debian 12/13、Ubuntu 22.04/24.04，amd64/arm64，要求 systemd 正在运行，Python >=3.10。依赖按需通过系统签名软件源安装。通过 SSH 使用时需要 TTY；批量工具可用 `ssh -t`。脚本可从 `/dev/tty` 读取输入，不依赖管道 stdin。

仓库源码安装器与 Release 附件均绑定到本项目公开仓库；未发布对应附件时下载会安全中止。手工修改管理器源码后必须重新打包并生成安装器，不能沿用旧 SHA-256。

## 菜单

菜单支持三种节点创建、列举、逐节点修改/删除/重启、分享链接、日志、内核与脚本升级、备份/恢复、证书更换和网络诊断。创建时才按需安装所选内核；没有预装的内核可在菜单 9 单独安装。

- Reality 默认建议 TCP 443，AnyTLS 建议 TCP 8443，HY2 建议 UDP 443。始终先查占用；TCP 和 UDP 同一端口号可以共存。
- 自动生成 UUID、密码、x25519 和 shortId；可输入自己的参数。Reality 私钥不出现在分享链接中。
- Reality 默认询问自有域名：提供域名时采用 **Caddy 证书 + 本机目标**，SNI、serverNames、证书域名一致；本机目标只监听 127.0.0.1，Xray 使用 PROXY protocol v1 转发。Caddy 可反向代理用户指定 HTTPS 网站，默认欢迎页减少资源消耗。无域名时才选第三方：通过 OpenSSL 实测三次可信证书/SNI、TLS 1.3、h2 和实际 X25519 组，按中位时延及波动排序；排除检测到的 CF。HTTP 跳转、403 等仅显示质量警告，不关闭证书校验。分 IPv4/IPv6 测最多各三个地址，失败继续其他 IP，保留未测/失败记录；自动推荐目标固定到本次验证的 IP，手动目标保留原输入。不改现有节点 SNI。详见 [Reality 流程](docs/REALITY_FLOW.md)。
- 证书菜单：1 HTTP-01、2 Cloudflare DNS-01、3 导入 PEM、4 明确标记的 30 天测试自签名。必须匹配 SNI；不生成 `insecure=1`。自建 Caddy 也支持四种方式，DNS-01 由独立 certbot 签发 PEM，使用官方 Caddy 即可。
- HTTP-01 必须确认全部 A/AAAA 指向本机及公网 TCP 80 可达；只检查 DNS 实际发布的地址族，IPv6 内核不可用不影响合法 IPv4-only 申请。IPv6-only 域名要求本机 IPv6 真正可用。不会停止已有 Caddy/Nginx。ACME 邮箱可留空，需要同意 CA 条款。
- Cloudflare DNS-01 无需 TCP 80，也不要求 A/AAAA 指向 VPS。Token 限定所需 Zone 的 DNS:Edit/Zone:Read；输入 Zone ID 和传播等待时间。NodePilot 使用发行版 certbot 插件自动创建/清理 TXT、等待传播；Token 隐藏输入，存独立 0600 文件，不写入节点元数据、分享链接、命令行或单元。独立 runner 在日志写入前脱敏，并隔离 SDK 全局凭据及 certbot 配置。真实 CA/DNS 签发仍待可用域名/Token 实测。
- 有效且域名未变的 certbot 证书优先复用，不因只改密码/端口再次申请；临期 30 天内续期。`nodepilot-renew.timer` 管理 AnyTLS/HY2 及 Caddy DNS-01 证书，成功后以新修订检查、重启/reload，失败保留旧修订。Caddy HTTP-01 仍由自身管理。菜单 10 可看详情、手动强制 certbot 续期或更换证书；强制续期受 CA 频率限制，Caddy HTTP-01 暂不提供手工强制签发。
- HY2 可配置 salamander、认证密码、SNI、UDP 端口和带宽提示。带宽值影响拥塞控制，不能提高实际链路容量，也不等同于硬限速。
- 只读展示 ss、内存与主机防火墙规则。**不会自动放行端口**；请在主机防火墙和云安全组自行放行准确的 TCP/UDP 端口。规则存在也不能证明公网 UDP 可达。
- 确认统一使用 y/n，支持 Y/N，错误输入会重新提示；危险操作默认 n，删除、卸载、接管和归还仍需两次确认。
- 菜单 14 只检查当前/最新稳定版本，不下载安装或重启；菜单 9 可安装/更新指定版本。首次按需安装内核也可选版本。
- 菜单 13 检查、导入和接管现有独立 Xray/sing-box，也可归还原服务。默认保留原服务，只有预检全部通过且两次确认后才切换，详细范围见下文。
- 节点列表显示 SNI/target、监听、证书状态和最近测试；服务检查结合 systemd Result/退出状态/重启次数及 MainPID 拥有的实际 TCP/UDP socket。修改前展示不含秘密的摘要并确认，提醒旧客户端链接可能失效。菜单 8 可做本机 Reality 代理 HTTPS 测试，始终单独标注公网未验证。
- 菜单 10 导出私有 Mihomo YAML，三协议字段按官方格式编码，保持 `skip-cert-verify: false`；自签名须在客户端导入信任。旧 Clash/不支持 AnyTLS 的客户端不能据此获得协议支持。

## 数据、安全与恢复

```text
/etc/nodepilot/
  .owner / .lock
  nodes/n-<随机ID>/
    current -> revisions/r-<随机修订>
    revisions/r-<随机修订>/node.json / config.json / certificate.pem / key.pem
    pending.json                 仅在事务中存在
  backups/                       私有、含凭据的 JSON 备份
  adoptions/a-<随机ID>/           私有原配置、单元、证书备份及持久化切换记录
  uninstall.json                 卸载阶段和恢复进度；不等同于原子卸载
  credentials/cf-<随机ID>.ini     0600 Cloudflare Token；不包含在节点导出中
  credentials/pending/          未提交 Token / 进程标记；成功部署后登记，失败或取消清理
  deletions/n-<随机ID>.json       删除最终清理日志；节点目录部分删除后仍可恢复
  certificate-status/ / probes/  私有续期状态及本机测试结果
  acme/n-<随机ID>/                独立 certbot 配置/账户/日志
  caddy/config.json / admin.sock  共享目标配置、私有 Unix 管理套接字
  caddy/storage/                 自建目标的私有证书/ACME 账户
/opt/nodepilot/
  app/<项目包SHA256>/             带安装文件清单的程序修订
  app/current                    原子选择当前程序版本
  program-pending.json           程序升级持久日志；候选文件逐个 SHA-256 检查
  cores/<xray或sing-box或caddy>/<版本>/   本项目校验内核
/etc/systemd/system/
  nodepilot-n-<随机ID>.service
  nodepilot-renew.service / nodepilot-renew.timer
  nodepilot-caddy.service         多个自建目标共用，最后一个删除时停止
/usr/local/bin/nodepilot
```

敏感目录 0700、配置与备份 0600。管理器退出后不驻留。分享输出明确提示含认证凭据，日志也可能含敏感信息。每个节点一个独立服务，使用唯一随机 ID；不改写 `/etc/s-box/sb.json`、`/etc/sing-box/config.json` 或机器原有 Xray 配置。

修改先备份，生成独立修订，经 `xray run -test -config` / `sing-box check -c` 校验，原子切换 `current` 使元数据和配置一致。启动后检查服务是否仍 active；失败自动回滚。事务记录落盘，中断后再次进入菜单恢复。多会话和证书定时操作使用同一文件锁。

Caddy 聚合配置另经 `caddy validate` 检查，通过私有 Unix 套接字 reload；管理器等待对应本机目标的证书、TLS 1.3 和 h2 就绪后才启动 Xray。失败时恢复节点修订和聚合配置。Caddy 不监听公网 TCP 443、UDP 443 或默认 2019 管理端口，不改写 `/etc/caddy/` 和系统 `caddy.service`。原有第三方 Reality 节点升级后保持原配置；主动修改目标时执行新的非 CF 筛选。

服务单元内容必须与项目生成内容一致才可停止/卸载；遇到外部修改时安全中止。程序卸载根据文件清单删除，不递归删除未知用户添加的程序文件。私有备份保留在 `/etc/nodepilot/backups/`；不卸载系统依赖，不删除其他软件的证书/服务，不改 IPv6/sysctl/全局网络参数。

卸载先检查全部单元和程序清单、保存私有备份，再停服务；删除配置前失败/中断，会在下次进管理器恢复原启用/运行状态。进入 `finalizing` 后只继续项目文件清理，不能恢复成原安装；若程序已部分删除，用可信 v0.4.0 或更新源码执行 `bash install.sh --local --upgrade` 修复后续做。候选期 main 的 v0.3.0 不能恢复新事务。此过程不是原子卸载。私有备份、接管记录、凭据和事务记录保留，确认不需要后自行清理；活跃接管仍需先归还。

恢复需要可信备份和**空节点集合**，逐节点配置检查并启动，失败回退已恢复节点。证书恢复为导入模式；不会误认为备份已迁移 ACME 账户或自动续期能力，需要在菜单重新配置 ACME。历史节点修订、备份和旧内核版本暂不自动清理；频繁更新需要注意磁盘空间。

## 接管与归还现有服务

仅接管 systemd 直接运行的独立 Xray/sing-box：单配置文件、1–16 个本阶段协议入站，每个入站一个用户、标准 direct/freedom 出站。复杂路由、其他协议、多用户、多配置目录、内置 ACME/DNS，以及 x-ui 等面板或包装脚本均拒绝自动切换，不会只迁移部分节点后停止整项服务。JSONC 注释/尾逗号可识别。

预检备份原配置、有效单元/drop-in、证书与服务启用/运行状态，复制 PEM 并执行所有入站的官方配置检查。切换保留原认证、监听端口和客户端 SNI，原文件不改写；原有 Reality 目标不重新选站。原日志输出改到独立 journal。为兼容已有客户端，只对导入节点允许保留原短密码或空 shortId，预览会提示更换；新节点仍使用强认证。

确认后停止所选原服务，全部新节点启动正常才停用原服务。操作中断或启动失败时撤回新节点并恢复原启用/运行状态；再次进菜单或续期任务会读取持久化记录恢复。菜单 13 归还时移除该批接管节点，恢复原服务，其他节点保留；归还失败尝试恢复当前 NodePilot 节点及其修改。外部修改原配置/单元/内核后拒绝自动切换或归还，保留备份供人工核对，不覆盖外部改动。原 PEM 合法续期可接受，但当前证书必须通过密钥、域名和有效期检查。

导入证书在 NodePilot 中是独立副本，**不会随原服务外部续期器更新**；应在菜单重新配置 ACME 或定期导入新证书。每个入站拆为一个内核进程，多入站服务接管后内存可能增加，256MiB 主机须评估节点数量。

存在未归还的接管记录时禁止卸载。节点备份同时保存私有接管记录，总大小限 8MiB；恢复菜单只恢复节点，不在新机器自动恢复对外部服务的控制权。原机器 `/etc/nodepilot/adoptions/` 需保留，归还操作以其中的记录为准。备份超限会明确中止，请先核对和归档历史记录。

## 下载与升级

内核只从官方 GitHub 稳定发布获取。API 必须提供附件 SHA-256，下载采用 HTTPS、超时、重试和大小上限，验证压缩包后仅提取所需内核，再核对 ELF 架构和执行报告版本。不提供校验值或校验失败会中止；不回退到执行未校验文件。

版本输入支持 `latest`、`list` 或完整版本号，输入 `0` 取消。列表分批读取官方最近 30 条发布；其他稳定版本可手工输入。最低 Xray 25.3.6、sing-box 1.12.0、Caddy 2.7.0，仍需通过当前节点原生检查和官方 SHA-256。v0.4.0 管理器拒绝选择旧脚本版本，以免旧代码忽略 DNS-01/卸载事务；候选期尚无可用 v0.4.0 稳定发布。

可用 `NODEPILOT_MIRROR=https://可信镜像前缀` 更换内核附件传输：官方失败后请求 `前缀/原始HTTPS-URL`，校验值仍来自官方 API。官方元数据不可达时安全中止；不绕过 TLS。SHA-256 + 官方 HTTPS 验证的是发布来源与下载一致性，不声称等价于独立维护者签名。

重复安装不改写节点；源码升级：

```bash
bash install.sh --local --upgrade
```

官方内核升级会先用候选内核检查全部受影响节点，再切换独立内核路径和逐个重启；失败回滚。管理器本身的在线更新只使用明确绑定的 GitHub 仓库，菜单 9 校验稳定 Release 安装器附件 SHA-256 后升级。

| 安装/绑定方式 | source.json 与更新策略 |
|---|---|
| 已校验的 Release 附件安装 | `official-release`，记录实际包 URL 对应的仓库；相同仓库正常升级不额外询问来源。URL 看起来属于 GitHub 并不赋予信任，仍须可信安装器、固定/API 摘要及执行前文件校验。 |
| `--local` 实际安装/升级 | `local-source`，在线更新关闭；可以继续使用可信源码 `--local --upgrade`。普通重复安装只是进入现有菜单，不改变来源。 |
| 显式用户绑定 | `explicit-user-bound`，仅经用户授权且稳定 Release 的安装器、项目包来源/摘要元数据有效后记录；绑定本身不执行更新。 |

从 Release/已有绑定切换到本地源码时，终端会显示来源变化，默认取消；确认后旧仓库存入非活动的 `previous_binding`，不会继续作为更新权限。由本地切换到 Release 或更换绑定仓库也需确认；拒绝时不切程序、不改来源、不增加备份。相同包且来源、launcher、owner、单元均一致时不重复安装。

本地安装需要在线更新时，先审查目标仓库，再显式授权（将 `owner/repo` 换成你信任的仓库）：

```bash
nodepilot bind-source owner/repo --confirm
```

此命令自行取得现有全局状态锁，不运行恢复或目录初始化。联网前检查程序升级、卸载、内核升级、节点修订/删除、接管/归还、临时凭据事务；活动、损坏、未知格式或链接记录均拒绝绑定。合法的 complete/rolled_back 卸载历史，以及稳定 active/returned/rolled_back 接管记录可以保留；prepared 接管尚未完成，需先人工核对。备份、旧修订和 program-pending-rc2.json 审计记录不作为活动事务。联网结束还会复查事务和原 source.json，防止覆盖校验期间的外部变化。绑定不重启节点、不迁移 owner、不改变证书/Token/内核/分享链接。未加 `--confirm`、仓库/稳定 Release 元数据异常、缺少唯一附件或官方 SHA-256 时不改来源。写入在 replace 后报错时，仅对仍等于本次写入的确切字节回退旧来源（或原来不存在的状态），不覆盖外部编辑/链接；若持续 I/O 故障或外部修改导致无法确认回退，会明确报错，操作者必须核对 source.json，不能假定旧来源已恢复。旧 v0.3/RC 的 `{"repository":"owner/repo"}` 继续可读并保留原绑定；旧记录不能证明当时到底来自本地还是 Release，不会自动猜测并清除。来源缺失、本地未绑定、损坏或异常链接时在线更新安全拒绝并提示处理方式。

`complete` 活动事务表示安装步骤已提交。恢复时会只读验证 current、目标哈希/必要模块、owner、launcher、单元、timer 和来源；一致后仅删除活动日志，保留历史证据。任一项目不符则保留日志并报告具体不一致，不靠重新安装或强行启用 timer 掩盖问题。

新程序事务使用 `transaction_version=4`。版本 3 只在 complete 保存链接证据，无法安全恢复非 complete 的旧启动状态；只添加可忽略字段会让旧管理器继续重放旧快照，因此需要新子版本明确拒绝旧管理器。外层 owner/schema、节点格式和 RC4 版本不变。版本 4 增加有界 `timer_context`（boot_id、两项状态、链接快照和 original/preserve/install 模式），保留不可变的 previous 原快照及 complete 证据。本版兼容 RC2 合法 installing、子版本 1/2/3/4；旧 complete 规则不变，缺失历史证据的非 complete 日志仍可按下述人工路径恢复，不伪造 boot_id 或删除原日志。升级中断应使用可信最新 RC4 源码安装器；旧 RC3/RC4 会拒绝版本 4，已安装旧 v0.3 入口仍无法追溯加固。

### complete 跨重启与人工核对

同一 boot_id（或 boot_id 不可读）保持 timer 两项状态及新日志启用链接的严格比较。启动周期改变时，仅在持久链接逐项未变、没有新 runtime 链接、状态符合原策略，而且 systemd 单调时间证据符合本次开机时自动激活/从未激活时，才自动最终化。enabled 保持 enabled，允许开机把 inactive 变为 active；enabled-runtime 自然消失为 disabled，disabled 的手动 active 自然变为 inactive。不把 runtime 启用永久化，不强制 start/stop/enable/disable/reload。程序、哈希、owner、launcher、单元及 source 的校验始终保留。

enabled 的 timer 必须不晚于 timers.target 首次进入 active，且二者无停止时间；未启用 timer 的进入/停止时间须为零。手动 start/stop/restart、disable/re-enable 链接替换、异常拓扑、缺少时间证据都会保守拒绝。此判定不提供完整系统操作审计；systemd 状态丢失、非标准启动顺序、文件系统变更也可能需要人工核对，不能只凭 boot_id 声称外部改动合法。

如果仅 timer 证据不匹配，应保留活动日志并按以下步骤处理：

1. 使用可信的本轮 RC4 源码，停止其他 NodePilot 管理操作；不要停止代理节点。将 `/opt/nodepilot/program-pending.json` 复制到 root 私有目录保存证据（权限 0600），不要上传日志或凭据。
2. 在本机核对日志 `phase` 确为 complete、`step`、事务版本、previous.timer、commit 和 boot_id。用 `systemctl show nodepilot-renew.timer timers.target -p UnitFileState -p ActiveState -p ActiveEnterTimestampMonotonic -p InactiveEnterTimestampMonotonic`、`systemctl cat nodepilot-renew.timer`、`journalctl -b -u nodepilot-renew.timer` 及 `/etc/systemd/system/timers.target.wants/`、`/run/systemd/system/timers.target.wants/` 的链接检查当前策略。核对是否确为自己的改动，或是否有启动证据缺失；原因不明则保留日志，继续诊断。
3. 人工确认保留**当前** timer 策略，而非恢复旧策略。对已核对的原活动日志运行 `sudo sha256sum /opt/nodepilot/program-pending.json`，保存并核对摘要；不要编辑日志、直接删除日志，或使用 enable --now 消除差异。
4. 执行 `sudo python3 /可信源码绝对路径/nodepilot finalize-program --review-timer <已人工核对的64位日志SHA256>`。这是对该份日志 timer 差异的显式确认，不代表略过程序检查。命令在全局锁内重新校验 complete/schema、精确摘要、程序及哈希、owner、launcher、单元、来源和受支持的当前 timer 状态，仅删除活动日志；不会写安装文件、签发证书、备份、reload 或改变服务。日志变化、损坏/未完成事务、程序/来源/单元差异仍拒绝；需要先核对可信原始文件和备份，不能用此命令修复它们。
5. 成功后再进入 `nodepilot` 菜单，确认节点监听和证书续期策略；没有真实连接验证时仍不能宣称节点联通。

此恢复路径适用于无 boot_id 的合法旧 complete 记录。若当前 timer 被 masked 或处于转换/未知状态，仍拒绝自动或人工最终化；由操作者明确处理后再核对。活动 preparing/installing/rolling_back 只能走原有可信安装器恢复，不能用人工确认跳过升级。完整主机重启与各发行版验证状态见 [RC4 验证报告](docs/RC_FOUR_VALIDATION.md)。

### 非 complete 事务跨重启恢复

preparing、installing、rolling_back 使用同一只读 timer 预检。已确认同一启动周期时保持原 RC4 的启用/运行状态恢复语义；持久化的 timer-intent、timer 和回滚检查点记录本项目操作范围。相邻检查点允许自身尚未落盘的单步写入，但不接受与阶段矛盾的 current、owner、launcher、单元或来源，不执行未完成复制或哈希不符的候选。

跨启动时复用 complete 的持久链接身份、runtime 链接消失及 systemd 启动时间判定。安全通过后，将**本次启动已经观察到的策略**持久化为 preserve，成功恢复与失败回滚均不再调用 restore_timer 重放旧值：enabled+inactive 可在新启动变成 enabled+active；enabled-runtime 的 active/inactive、disabled+active 均可变为 disabled+inactive。后续恢复即使仍在同一次新启动，也继续保留这份策略；再次重启须重新验证。程序文件可按升级/回滚需要写入及 daemon-reload，但不因此重启代理节点。

首次安装没有旧 timer，属于单独分支：未完成部署仍按原授权完成默认 enabled+active；失败时只清理本次创建的续期单元和链接。清理会必要地停止这个**尚未安装成功的自有 timer**，不操作原有或外部 timer。若 enable 已完成但证据尚未落盘就重启，不能确认链接由谁创建，会保留日志等待人工核对。preparing 回滚不执行不完整候选；已有版本升级的正常跨启动恢复/回滚不发 timer enable/start/stop/disable。

旧版本 1/2 缺少 boot_id，或版本 3 跨启动但没有原链接快照时，默认保留日志并拒绝猜测；版本 3 仍可在已证明同一启动时按原规则恢复。RC2 本就没有旧 timer 快照，仅保留其原始证据并观察当前策略，不将观察时 boot_id 冒充事务开始 boot_id。迁移或跨启动重新确定策略前，在 root 私有目录保留带原日志 SHA-256 前缀的 `program-pending-vN-<摘要>.json`；只有 program-pending.json 是活动日志，审计副本不阻止正常管理，生产卸载也不自动删除它们。

若因缺证据、重启后用户主动操作而无法自动恢复：

1. 停止其他管理操作，私有复制活动日志，核对其 phase/step、current、owner、launcher、原/目标单元、来源、timer 状态/启用链接与本次启动日志。沿用上节只读 systemctl/journalctl 检查；当前策略原因不明时保留记录继续诊断。
2. 明确同意**继续日志中既定的升级或回滚，并保留已核对的当前 timer 策略**，计算并手工核对活动日志 SHA-256。preparing 将回滚准备工作，installing 将继续已校验候选，rolling_back 将继续原回滚；这不是把它标成 complete，也不跳过文件、所有权、来源或原生候选检查。
3. 使用已审查的本轮源码，在隔离 Python 导入和现有全局锁内调用既有恢复函数；将下面两个参数替换为可信源码绝对路径及已核对摘要，不自动取“最新摘要”代替确认：

```bash
sudo python3 -I - /root/Node-Pilot-trusted '已核对的64位日志SHA256' <<'PY'
import sys
from pathlib import Path
sys.path.insert(0, str(Path(sys.argv[1]).resolve(strict=True)))
from nodepilot_pkg.common import root_required
from nodepilot_pkg.manager import Manager
from nodepilot_pkg.deploy import recover_program
root_required()
manager = Manager(migrate_owner=False)
with manager.locked(migrate_owner=False, existing_only=True):
    recover_program(manager=manager, reviewed_timer=sys.argv[2])
PY
```

4. 若恢复的是旧候选，再用同一可信最新 RC4 源码执行 `bash install.sh --local --upgrade` 部署当前修复后进入菜单，核对节点监听与续期策略。没有实际连通验证时不声称节点联通。

此参数仅接受精确活动日志摘要和受支持的当前 timer/标准自有链接。masked、转换状态、外部链接、坏 schema/检查点、候选哈希/来源/文件不符仍拒绝，原活动日志保留。人工确认后再次中断且证据不足时，需要重新核对新摘要；回滚失败继续保留 rolling_back，不报已完全恢复。complete 仍只能使用原有 finalize-program，只读、不重装、不新增备份。真实整机重启结果与模拟结果分开记录。

## 客户端

生成标准 `vless://`、`anytls://`、`hysteria2://`，正确编码密码、query 和中文名称，IPv6 加方括号。HY2 链接不携带机器相关的带宽值。自签名必须手动在客户端导入节点 `certificate.pem` 为信任证书，URI 不能单独传递完整信任关系；不会默认关闭证书校验。

导出 URI 的 sing-box 1.14.3 三协议已完成真实传输，Reality 另有 Xray 26.3.27 验证。Mihomo 1.19.32 官方内核通过三协议 YAML/IPv6/转义字段解析；其 GUI、公网连通及 v2rayN、Egern GUI 矩阵仍未验证。不要推广成所有客户端兼容。YAML 是 proxies 片段，需要合入客户端自己的分组和规则；不提供订阅服务器。

## 1c / 256MiB

管理层仅使用 Bash 和 Python 标准库，无 pip、数据库、Web 后台或常驻管理进程；流式下载，预编译内核，不在 VPS 编译，也不下载 geodata。服务默认 `GOMAXPROCS=1`、`GOMEMLIMIT=64MiB`、`GOGC=50`；自建目标共享一个 Caddy，软目标 32MiB，关闭 HTTP/3 以保留 HY2 UDP 端口。

`GOMEMLIMIT` 是 Go 内存软目标，**不是 RSS 硬上限**。v0.1.0 的单核、192MiB、禁 swap 冷启动三内核测试组峰值约 171.5MiB，无 OOM；该旧结果不包含新增 Caddy，不能作为 v0.2.0 四进程资源证明。实际 VPS 为 1c/2GiB；测试组不包含完整 OS、SSH 和 ACME 续期，仍不能宣称完整 256MiB 机器长期验收通过。正式服务没有自动设置硬内存限制、swap 或全局 sysctl。

v0.2.0 已补测同样单核/192MiB/禁 swap 的冷启动组，含 Xray、两个 sing-box、共享 Caddy 和采样 Python，三协议各 8MiB、最多 4 并发及 HTTPS 全部通过。没有 OOM，但内存曾触及 192MiB 上限并发生回收压力（memory.events max=322）；这不是自然峰值或完整 256MiB 整机验收。ACME 签发/续期和长期负载仍需另测。

v0.4.0 补测 256/512/1024MiB cgroup，各含单协议和三协议，共 12 组，无 OOM/无 swap。包括配置检查、重启、OpenSSL 测试证书生成和加密请求；256MiB 三协议组还执行 sing-box 官方候选下载/校验/替换启动。该组峰值触及限制、max=698，不能证明完整 256MiB 整机有足够余量。ACME 工具实际签发、整机及长测仍待验证，详见验证记录。

## 开发验证

在 Linux 项目目录：

```bash
python3 -m unittest discover -s tests -v
bash -n install.sh
shellcheck install.sh
python3 nodepilot self-check
python3 tools/native_check.py --download
python3 tools/mihomo_check.py --download
```

最后一条仅将已校验官方内核保存到忽略的 `.test-tools/`，执行真实配置检查，不部署节点。后续可不带 `--download`。

可选 root/systemd 检查（有真实本机副作用，务必在测试系统运行）：

```bash
sudo python3 tools/service_smoke.py
sudo python3 tools/install_smoke.py
```

服务检查只创建 `/run` 临时服务和 loopback 监听，测试结束删除本次节点与单元；安装检查拒绝所有已有 NodePilot 路径，测试 `/opt`、`/etc`、命令、timer 的安装/升级与 TTY 后清理。它们不验证公网连接。

离线生成（元数据 schema 见 [架构](docs/ARCHITECTURE.md)）：

```bash
python3 nodepilot render /root/node.json --output /root/config.json
python3 nodepilot share /root/node.json --show-secrets
```

GitHub Actions 会执行上传范围/秘密指标检查、单元测试、shellcheck、官方配置校验及可复现打包；以仓库实际运行结果为准。

## 发布一键安装器

发布前先按 [上传隐私检查](CONTRIBUTING.md) 审查源码、提交身份、Git blob 和附件；本仓库示例：

```bash
python3 tools/package.py --repo km-hl/Node-Pilot --tag v0.4.0
python3 tools/release_audit.py --repo km-hl/Node-Pilot --tag v0.4.0
```

打包只生成 `dist/nodepilot-0.4.0.tar.gz`、校验文件和带**固定附件 URL / SHA-256**的 `dist/install.sh`，不自动上传。候选包不等于已发布稳定版；将生成安装器同步到源码，提交后对比 Git HEAD。Release 必须包含 `install.sh` 附件才能在线升级。正式发布后对应入口：

```bash
bash <(curl -fsSL https://github.com/km-hl/Node-Pilot/releases/download/v0.4.0/install.sh)
```

修改项目文件后必须重新打包和生成校验值。项目包仅含显式允许的管理器文件，不包含运行时配置、测试内核或凭据。

## 范围与许可

本阶段不实现 XHTTP/CDN、Trojan/VMess/Shadowsocks、Web 面板、多机管理或订阅服务。流程参考需求列出的教程，协议配置以官方文档为准，不复制第三方一键脚本。源码采用 [MIT License](LICENSE)；下载的 Xray/sing-box 使用各自上游许可证。
