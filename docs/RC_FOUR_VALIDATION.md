# v0.4.0 RC4：安装器事务保护与发布前验收

本页先保留 RC4 初版历史证据（行号对应当时提交），文末“RC4 收尾”记录基于 `667c42f` 的最新修复、当前行号与重跑结果；不创建 RC5。正式发布判断以收尾节为准。

日期：2026-10-15。分支 `codex/v0.4.0-stability`，现有 [PR #1](https://github.com/km-hl/Node-Pilot/pull/1)。开始前 fetch 并核对 GitHub，最新提交仍为审查基线 `5a7a6ab83d51bcef1d1d6bd1956693f54def8f58`。修改前 Linux 完整基线 159 项通过。PR 保持草稿，不合并 main，不创建 Release。

## 本轮缺陷、位置与行为

| 位置 | 修复前触发条件及影响 | 修复后 |
|---|---|---|
| `install.sh:32` | 已安装且不带 --upgrade 时直接 exec 旧菜单；新 intent 已写、current 未切换时，旧 v0.3 无法理解新日志。 | 所有非升级入口先检查活动 program-pending.json（含悬空链接），直接拒绝快捷菜单并提示可信 RC4 源码 --local --upgrade；不读取候选、不猜测日志合法性、不删除原记录。历史 program-pending-rc2.json 不参与此判断。 |
| `nodepilot_pkg/deploy.py:293` | 新安装器恢复成功后又创建同一包的升级事务及备份；重复 --upgrade 也重复写单元。 | 校验当前摘要目录与本次可信 payload 的逐文件哈希，核对 launcher、owner、单元完整性后直接返回；不产生额外备份，不 reload/restart，不改变用户 timer。 |
| `nodepilot_pkg/deploy.py:120,151,218` | current 已切换后，旧修订缺失/链接未在前向恢复前拒绝；清单可自洽但缺少关键模块；记录版本未与源文件比较；特殊日志可能阻塞读取。 | 复用原校验，补上旧入口/包目录链接和缺失检查、必要管理器模块检查、已有记录版本与实际版本比较；活动日志须为普通文件且不超过 256KiB。异常在执行前拒绝、保留原日志。旧合法记录没有版本字段时不虚构历史。 |
| `nodepilot_pkg/cli.py:51`、`nodepilot_pkg/menu.py:546` | 获取启动锁时可能先迁移 state owner，再拒绝损坏事务。 | 启动恢复使用 locked(migrate_owner=False)，恢复通过后仍在锁内迁移 owner。无效事务不先改变原 owner。 |
| `tests/test_bootstrap.py`、`tools/bootstrap_smoke.py` | 缺少安装器自身绕过恢复、四版本 PTY 恢复及重复备份验收。 | 增加真实 Bash（路径重映射）、故障注入和可销毁环境真实 systemd/PTY 测试；原 RC3 六状态测试全部保留。 |

只调整安装与恢复相关代码，没有改代理协议、SNI/CF 筛选、证书算法、凭据生命周期、Caddy 目标、订阅或 GUI，也没有增加常驻进程或依赖。

## 入口与信任边界

| 入口 | 活动事务存在时 |
|---|---|
| RC4 `bash install.sh`、`bash install.sh --local` | 保守拒绝；即使 current 已是 RC4 也不猜其能力，不偷偷升级。正常无事务的重复安装继续原菜单。 |
| RC4 `bash install.sh --upgrade` | 下载成功且包 SHA-256/路径/文件检查通过后，使用可信 payload 的 Python 恢复入口，锁内恢复成功才继续。候选附件尚未正式发布，当前默认在线 URL 的可用性不作成功承诺。 |
| RC4 `bash install.sh --local --upgrade` | 使用用户明确信任的完整源码打包、校验和恢复，拒绝未知/损坏/越界事务；不调用旧管理器理解新状态。 |
| 已安装 RC4 的 `nodepilot`、`nodepilot menu`、`nodepilot renew` | 使用已有 Python 恢复入口并持有状态锁；先验证/恢复，再迁移 owner、进行管理或续期。 |
| 已安装旧 v0.3 的同名命令或旧安装器 | **无法追溯保护。** 旧二进制/脚本没有 RC4 守卫，尤其 preparing 时可能仍允许旧操作；必须使用可信 RC4 新安装器恢复，不能宣称旧入口已完全安全。 |

Bash 只做“拒绝快捷路径”的存在性检查，不解析 JSON、不实现第二套状态机，也不把历史审计文件当成活动事务。真正恢复继续使用 recover_program / validate_record / verify_candidate、文件哈希、原状态快照和回滚。未知 schema、链接或损坏内容保留原证据；普通错误回滚和回滚失败后重试沿用 RC3 行为。对缺失历史信息的 RC2 事务仍采用可观察策略，不倒推用户过去的启用状态。

## 新增测试与实际结果

本轮环境：本机授权可销毁 Debian 13 amd64 WSL，真实 PID 1/systemd 和 PTY。不是公网 VPS，不代表整机重启、断电、arm64 或 256MiB 主机。

- **最终 165 个 test 方法通过、无 skip**，RC3 基线 159 项；新增 6 个方法，内部覆盖正常重复/历史证据、8 类快捷路径活动或异常、六种 timer 策略下多次同包恢复、缺模块/旧修订异常/版本不符、菜单及 renew 拒绝前不迁移 owner、FIFO/目录/超大日志拒绝。路径重映射 Bash 测试不调用真实 systemd；状态模拟器不冒充实机。
- **四版本 × 两个中断阶段，共 8 组真实 systemd/PTY 验收通过**：v0.3.0、RC1、RC2、RC3 分别在 intent 和 current 阶段中断。普通重复安装保持版本/文件/timer/备份；活动日志阻止快捷菜单；可信新版恢复后可进入菜单，再次恢复/同包升级不变更状态或增加备份；仅有历史证据仍正常启动。测试 timer 特意为 disabled + active。
- **8 类异常各检查两条入口**：损坏 JSON、未知 schema、活动日志 symlink、候选缺失、SHA-256 不匹配、必要模块缺失、旧修订链接、owner 不符。普通安装器及可信 --local --upgrade 均拒绝；活动日志/外部证据、原节点 sentinel、owner、launcher、单元和 timer 保持预期，备份不增加。
- 一组 --upgrade 分支使用临时 curl 替身提供本地可信包，真实执行摘要校验/安全解包/部署/菜单；**只验证在线分支控制流，没有冒充公网下载未发布的 RC4 附件**。
- **36 组真实 timer 矩阵通过**：v0.3 与 RC3 到 RC4，各六状态 × 成功/最终 self-check 失败/daemon-reload 后中断恢复；实际检查 UnitFileState、ActiveState、`/etc` 与 `/run` 启用 symlink，失败后旧文件逐字节恢复。首次成功与失败清理另有 2 组，临时单元及链接清理通过。
- 真实安装 smoke：自动 TTY、无事务重复安装、同包升级、cwd 同名模块隔离、无效输入循环、stdin 管道和卸载通过；可信 v0.3 安装器升级另跑通过。
- 三协议服务、TCP/UDP 同端口合法共存、服务启动失败回滚、单节点删除隔离通过；Caddy 本机 IPv4、IPv6、双栈高端口 HTTP/PID 检查通过。旧 v0.3 三节点 metadata/URI/原生配置/MainPID 监听保持、端口修改恢复、旧管理器拒绝 schema=2 通过。
- 官方下载 SHA-256/ELF/版本检查及 **8 项原生配置检查通过**：Xray 26.3.27、sing-box 1.14.3、Caddy 2.11.7。官方 Mihomo 1.19.32 三协议 YAML 原生解析通过。配置解析不等于真实代理连接或 GUI 导入。

已运行命令（测试工具和日志仅在忽略目录保留）：

```bash
python3 -m unittest discover -s tests -v
bash -n install.sh
shellcheck install.sh
python3 nodepilot self-check
python3 tools/native_check.py --download
python3 tools/mihomo_check.py --download
python3 tools/bootstrap_smoke.py --disposable --legacy-source .test-tools/legacy-source \
  --legacy-source .test-tools/rc1-source --legacy-source .test-tools/rc2-source --legacy-source .test-tools/rc3-source
python3 tools/program_smoke.py --disposable --legacy-source .test-tools/legacy-source --legacy-source .test-tools/rc3-source
python3 tools/install_smoke.py
python3 tools/install_smoke.py --legacy-source .test-tools/legacy-source
python3 tools/upgrade_smoke.py --legacy-app .test-tools/legacy-source
python3 tools/service_smoke.py
python3 tools/http_family_smoke.py
```

ShellCheck 使用项目已提取的 0.10.0，未安装新系统依赖。中断通过受控 SystemExit 注入，**未进行真实掉电/SIGKILL 全矩阵或 WSL/VPS 重启**。本轮未访问或修改生产 VPS；没有修改全局网络、防火墙、IPv6、sysctl、路由或 swap。

## v0.3.0 升级兼容

新安装器无活动日志时不会强制升级 v0.3；有日志时不进入它的旧菜单。四版本验收使用可信 Git 源码：v0.3 `548e84202589dc0be5898a02552541a0065a6de5`，RC1 `bc253aa743ae1edc3657e46dbe8bf0977ac424e9`，RC2 `507835ee695d86a06b5fbcc332c3ee3043bf9924`，RC3 `5a7a6ab83d51bcef1d1d6bd1956693f54def8f58`。节点元数据 schema 未变；保留旧 timer 的独立启用/运行策略、原分享链接及用户配置。旧入口不可追溯保护的限制如上。

## Cloudflare DNS-01 验收状态

**DNS-01 真实签发及续期：等待授权测试环境。** 当前会话没有明确授权测试域名/Token；本轮 Debian WSL 也未安装 certbot / certbot-dns-cloudflare / cloudflare / acme Python 包。未搜索或使用其他项目的私密凭据，没有向 Cloudflare 或 CA 发起真实签发。

已将当前参数与官方文档核对，未发现需要为本轮更改协议实现的明确不兼容；这只是接口审查，不能证明发行版插件实际运行兼容：

- [Certbot 官方用法](https://eff-certbot.readthedocs.io/en/stable/using.html)：certonly、独立 config/work/log 目录、server 与续期参数仍有对应接口。
- [Cloudflare DNS 插件](https://certbot-dns-cloudflare.readthedocs.io/en/stable/)：INI Token、credentials 路径及 propagation-seconds 对应当前实现；插件负责 TXT 创建/清理。Token 仅限测试 Zone 的 DNS:Edit，本项目只读归属验证还需要该 Zone 的 Zone:Read。
- [Cloudflare Zone Details](https://developers.cloudflare.com/api/resources/zones/methods/get/) 与 [DNS Record API](https://developers.cloudflare.com/api/resources/dns/subresources/records/methods/create/)：当前 Zone v4 GET 和 Bearer Token 方式匹配接口；没有实际验证 Token 权限、TXT 写入、传播或故障清理。
- [Let's Encrypt Staging](https://letsencrypt.org/docs/staging-environment/)：真实验收应先使用 Staging，测试证书需显式信任测试链，不关闭校验。当前生产 acme() 固定正式 CA；获得测试环境后需先在隔离测试副本审核 Staging 定向，不能直接对生产域名反复签发。
- [Caddy PEM load_files](https://caddyserver.com/docs/json/apps/tls/certificates/load_files/)：certificate/key 字段与当前生成配置匹配，官方内核原生检查通过；不能据此宣称真实 ACME 新证书加载及续期已完成。

Zone 归属、TXT 创建/传播/成功与失败清理、Staging/正式签发、证书/私钥/SNI、TLS 握手及 Caddy Reality 目标、续期、真实插件 Token 脱敏和失败保持旧节点，这 12 项真实链路仍全部待授权验收。已有 OpenSSL 与故障注入仅保留为局部证据，不改写为真实签发结果。

## 代码审查结论与剩余 P0/P1/P2

审查覆盖写入顺序、锁、源码/候选校验、旧/新修订、owner、launcher/current、单元、timer、回滚及再次恢复、首次失败清理。上表列明本轮修复和对应测试，未发现本轮范围内另一个尚未修复的明确 P0。并非对整个项目或所有外部环境作无缺陷保证。

| 等级 | 剩余问题或验收缺口 |
|---|---|
| P1 发布阻塞 | DNS-01 的真实签发/续期/故障 TXT 清理/实际插件脱敏未完成；必须等待授权测试环境。 |
| P1 操作边界 | 已安装旧 v0.3 管理器或旧 installer 不具备 RC4 守卫；升级中断后必须用可信新版恢复。旧 RC2 未记录的历史不可恢复；损坏证据需要人工核对，不能绕过校验。 |
| P2 环境矩阵 | 本轮只有 Debian 13 amd64 WSL；Debian 12、Ubuntu 22.04/24.04、arm64、真实 IPv4-only/IPv6-only/公网双栈、云防火墙、整机重启及既有 Caddy/Nginx 的完整部署矩阵未验证。 |
| P2 内存 | 256MiB/512MiB/1GiB 整机及真实 ACME 峰值、长期负载未重测；三协议此前触及 256MiB cgroup 限额的边界不变。标准库/无常驻新进程不能替代整机性能证明。 |
| P2 客户端/接管 | 本轮 Mihomo 仅原生解析；sing-box 真实代理传输、v2rayN/Egern GUI、独立服务接管/归还的新一轮真实验收未执行。既有单元测试保留，历史结果不能冒充本轮重跑。 |

## 发布判断与候选产物

**建议继续保留 RC4 稳定候选并完成剩余验收，不建议进入正式 v0.4.0 发布、合并或创建 Release。** 已授权本地稳定性修复完成，外部条件不足时不降低标准。

本地可复现候选包 SHA-256：`a01624010026f3689b3525f8571970eca93dfaa0ef6e99350f4954d45fcdfc00`，installer 重新绑定同一摘要。候选附件仍未正式发布；源码 --local 验证不证明默认在线下载 URL 当前可用。dist、内核、测试凭据、私钥、节点配置和原始运行日志不上传。

## RC4 收尾：complete 最终化与安装来源（当前结论）

收尾基线 `667c42f6e86a36a59ce5dc2fcc179f87af7af670`，开始前 fetch 和 PR 核对确认没有新提交，修改前 165 项测试通过。本节覆盖本轮最新代码与重跑；不以 RC5 命名，不创建新 PR/Release，不合并 main。

### 修复与代码位置

| 等级/位置 | 触发、影响与修复 |
|---|---|
| P2 `deploy.py:202,265` | phase=complete 已持久化、日志删除前中断，原恢复会再切 current、写 owner/launcher/单元、reload、self-check、恢复 timer。现独立进入只读最终化，逐项核对实际状态后仅 unlink 活动日志并 fsync。异常不重装、不自动修复、不回滚，不删除证据。 |
| P2 `install.sh:81`、`project_update.py:14,48,68` | --local 原先仍传默认 Release URL，修改版/Fork 因而错误绑定上游。现本地打包后清空 URL，持久化 local-source；来源切换需 TTY 明确确认、默认取消，确认在锁内且先于备份/程序改写。 |
| P2 `project_update.py:87,102`、`cli.py:20,32` | 安装来源与更新授权未区分。新增显式 bind-source --confirm，校验仓库及稳定 Release 的 install.sh、项目包唯一附件/精确 HTTPS URL/官方 SHA-256 元数据后才记录授权；绑定不执行升级。异常/未绑定来源不得继续更新，活动程序事务不得修改绑定。 |
| 附加收紧 `releases.py:19`、`project_update.py:75` | 拒绝仓库路径的 `.` / `..`、来源 schema 布尔/异常类型、链接/非普通/超大来源文件，以及附件 URL 不匹配目标仓库/tag/name、userinfo/query/fragment 或异常 digest。旧合法格式保持兼容，不通过 URL 外观推断代码可信。 |

complete 只读核对：current 指向目标、目标目录/清单/必要模块/逐文件哈希和版本、程序及状态 owner、launcher、两个单元准确字节、无外部 FragmentPath/drop-in/异常链接、timer 两项独立状态、source.json 与事务预期。所有命令只允许 systemctl show；不执行候选 Python、不新建备份、不写安装文件、不 reload/start/stop/enable/disable。旧修订已经退出执行路径，最终化不依赖再次安装它。

成功后只删除活动 program-pending.json，program-pending-rc2.json 历史证据保留。删除失败时保留日志，下一次仍只验证/最终化；状态不一致报告相应项目并保留证据供人工诊断。若在 complete 后主动修改了 timer/来源等状态，也会拒绝，不能将它当作“授权重新安装”。可信安装器的包下载/解包/入口自检仍是独立的供应链检查，不等于 complete 恢复再次执行安装。

### 来源数据与迁移策略

复用 source.json，新增结构化 provenance，不改节点 schema、owner schema 或事务外层 schema=2。新事务以 transaction_version=2 强制包含 source_after，回滚仍逐字节恢复 previous.source；继续读取旧子版本 1，没有新字段时按原 repository/原 source 快照验证和恢复。附加审查发现旧 RC4 会忽略未知来源字段，因此必须升级子版本让旧 RC3/初版 RC4 明确拒绝，不能只增加可忽略字段。已用可信旧 RC4 检查拒绝行为。

| 记录 | 权限/迁移 |
|---|---|
| `{"schema":1,"provenance":"official-release","repository":"owner/repo"}` | 可信 installer 已完成包 SHA-256 与安全解包后，依据真实传入的严格 Release 附件 URL 记录仓库；并非 URL 本身提供执行信任。相同仓库升级无需额外来源确认。 |
| `{"schema":1,"provenance":"local-source"}` | 本地源码默认状态，不推断 cwd/Git remote/模板 URL；没有在线更新权限。 |
| local-source 加 `previous_binding:{"repository":"owner/repo"}` | 从已有绑定转本地须确认，旧仓库保留为非活动历史，不默默丢失也不继续授予更新权限。 |
| `{"schema":1,"provenance":"explicit-user-bound","repository":"owner/repo"}` | 用户审查仓库后通过 `nodepilot bind-source owner/repo --confirm` 授权；稳定 Release 附件元数据须通过验证。此操作只修改绑定，不执行新程序。 |
| 旧 `{"repository":"owner/repo"}` | v0.3/RC 兼容读取，旧绑定继续有效且纯读取不改原字节。旧格式不能证明当时来自本地还是 Release，不自动猜测并抹除；主动以本地源码升级时提示确认并保留非活动历史。 |

Release → 本地、本地 → Release、仓库 A → B 均要求必要确认；拒绝在任何程序改写和新备份之前返回。来源一致且同包完整则维持无副作用的快捷返回。损坏/未知来源保留文件并拒绝，不用“未绑定”掩盖异常。来源缺失/本地未绑定时在线查询直接提示本地升级或显式绑定，网络发布请求不会发出。

### 本轮实测

最终 **179 项测试通过，无 skip**（基线 165，新增 14 个方法，子场景单列），既有协议、卸载、Cloudflare Token 生命周期、旧元数据、timer 六状态回归全部保留。

- `CompleteProgramTests`（`tests/test_release_candidate.py:653`）：六种 timer 状态正常 complete 只删除日志；current inode、owner/launcher/单元/source 的字节及 inode/mtime、备份集合不变，历史证据不删，第二次恢复无待处理。12 类错误覆盖 current、哈希、缺模块、launcher、service/timer、timer 两项状态、两处 owner、来源及来源链接；全部拒绝且日志原字节保留。另验 unlink I/O 错误后重试、旧来源格式 complete 兼容。
- `tests/test_project_source.py`：官方/本地首次、旧 v0.3/RC 来源、授权/拒绝迁移、非活动历史、本地继续升级、回滚原 source 精确字节、无绑定时不联网、损坏/链接/异常 schema/仓库、显式绑定确认/附件校验/活动事务拒绝。
- **真实 Debian 13 amd64 WSL/systemd/PTY**：原四版本 × intent/current 的 8 组继续通过，新增每个旧版本的 checked/complete 最终化，四基线共 12 组；另以初版 RC4 为第五基线执行三阶段，共 **15 组跨版本中断恢复**。其中 **5 组 complete** 实际限制为只读 systemctl show，禁用所有安装写入函数，核对 inode/mtime 未变；旧 RC4 当前管理器会拒绝新的子版本 2，日志和原状态保持。
- **真实来源流程** `tools/source_smoke.py`：修改过的 Fork 源码且包含 Git remote，首次/重复本地安装不推断上游；本地 → Release 和 Release → 本地分别拒绝/确认；相同 Release/local 重复升级不增备份；旧绑定转非活动历史；Release 首次 provenance 正确。**Release 传输使用临时 curl 替身，实际 SHA/解包/部署/systemd，但未下载尚未发布的公网 RC 附件**。显式绑定的远端 Release API 元数据使用单元夹具，不冒充真实仓库验证。
- 36 组真实 systemd 六状态成功/失败/中断矩阵及 2 组首次成功/失败通过；安装/旧安装器/TTY/管道/卸载 smoke、旧 v0.3 三节点 metadata/URI/配置/监听、真实启动失败回滚、删除隔离和 Caddy 本机 IPv4/IPv6/双栈 HTTP/PID 检查重跑通过。
- bash -n、ShellCheck 0.10.0、self-check 通过；官方 Xray 26.3.27、sing-box 1.14.3、Caddy 2.11.7 再下载并通过 SHA/ELF/版本及 8 项原生配置检查；官方 Mihomo 1.19.32 三协议导出解析通过。配置检查/解析不能替代 GUI 或真实公网连接。

本轮完整命令沿用上文所列；bootstrap_smoke 内增加 checked 场景，另执行 `python3 tools/source_smoke.py --disposable` 及 `python3 tools/bootstrap_smoke.py --disposable --legacy-source .test-tools/rc4-before-source`。私有日志为 `.test-output/rc4-close-*.log`，不上传。所有系统级测试都在已确认无 NodePilot 安装的可销毁 WSL 进行，拒绝生产路径；结束清理临时节点、单元、安装和启用链接。真实中断仍为 SystemExit 注入，不是断电/整机重启。

### 聚焦 Code Review 与发布判断

再次核对所有安装/升级入口、owner/状态锁、包和候选执行前校验、complete 副作用、日志删除顺序、回滚失败保留、来源变更与凭据隔离、外部 systemd/配置边界。已知两项 P2 已修复；确认取消发生在备份前、来源提交在最终自检后而 complete 之前、回滚恢复原字节，最终化异常不被普通安装 catch 掩盖。本轮范围未发现新的未修复明确 P0/P1 代码缺陷；不等于对全项目/全部外部环境作无缺陷承诺。

**建议冻结功能并进入最终发布验收，继续保留 RC4；尚不建议正式发布/合并。** 外部验收缺口保持原样：真实 Cloudflare TXT 创建/传播/清理、Staging/正式签发及自动续期、实际插件脱敏、部分公网 IPv6、VPS 重启、Debian/Ubuntu/arm64 完整实机、256MiB 长期整机和 GUI 客户端，均不能由本轮 Mock/WSL 结果代替。未查找或使用其他项目的隐私凭据，未访问生产 VPS，未修改全局网络/sysctl/swap，也无新增协议/常驻服务/无关依赖。

收尾候选包 SHA-256：`5ae17c93f8e7cb570950e9f2e6626b5596b11d3597a7840c505dbb7d00259346`。安装器重新绑定，Windows/Linux 可复现包一致，提交后对照 Git HEAD 做文件审计；不上传 dist、内核或运行数据。前文 a016… 为初版历史产物，当前以本节摘要为准；不创建 Release 或 RC5。
