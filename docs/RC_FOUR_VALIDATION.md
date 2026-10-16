# v0.4.0 RC4：安装器事务保护与发布前验收

本页保留历次 RC4 历史证据（行号对应当时提交）。**当前结论以文末“RC4 授权 VPS 补测”一节及 VPS 报告为准**；手动选站修复基线 `184653e`，实机测试基线 `c63106f`，不创建 RC5。前文测试、产物摘要和环境结论不能代替最新节的本轮验证。

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

收尾候选包 SHA-256：`5ae17c93f8e7cb570950e9f2e6626b5596b11d3597a7840c505dbb7d00259346`。此摘要现为历史产物；当前以文末最终事务边界修复节为准，不创建 Release 或 RC5。

## RC4 最终事务边界修复（历史结论）

2026-10-15，本轮开始实际 fetch，远端开发分支及草稿 PR #1 仍为审查基线 `791af460de10d5cb7911c57ba510e9d52ddd27db`；两个问题仍存在。仅在 `codex/v0.4.0-stability` 修复，不创建 RC5、不合并 main、不发布稳定 Release。

### 修复及代码位置

| 等级/位置 | 问题及行为 |
|---|---|
| P1 `installation.py:36,45,77,81`、`deploy.py:79,233,381` | 原 complete 严格比较两项 timer 状态，无法区分重启后的正常变化。新事务版本 3 保存开始/提交 boot_id、原策略、提交实际状态和启用链接身份；同次启动严格核对，跨启动使用单独证据规则，始终只读。 |
| P2 `project_update.py:88`、`manager.py:87`、`cli.py:34` | 原绑定只检查程序日志，可能与卸载/内核/节点/接管事务冲突。绑定函数自行加全局锁，在联网前后只读检查相关事务及来源；不触发恢复、停止服务或改动凭据。 |
| 附加 P2 `deploy.py:390` | complete 持久化之后，提交调用返回 I/O 错误可能落入回滚路径。现不回滚已到 complete 的内存记录，保留已持久化阶段供下次验证恢复；注入 durable complete 后错误，证明不会反向切 current。 |
| 附加 P2 `manager.py:46`、`deploy.py:266` | 新的只读恢复/绑定入口若复用初始化锁，会创建缺失状态/辅助目录。改为 existing_only 锁，缺失状态/owner/锁拒绝；不创建目录、不迁移 owner。锁使用 O_NOFOLLOW，拒绝非普通文件/硬链接，不在获锁前 chmod 外部路径。 |
| 附加 P2 `project_update.py:110`、`test_project_source.py:260` | 来源原子 replace 成功后 fsync 报错，原先会返回失败但留下新来源。现仅在当前仍为本次确切写入时恢复旧字节/原缺失状态；外部编辑/链接不覆盖，回退失败明确报告未确认，不报成功或声称原来源已恢复。 |
| 附加 P2（测试工具）`tools/caddy_smoke.py:41`、`tools/stability_smoke.py:49`、`tools/adoption_smoke.py:57` | VPS 工具缺失 SSH_CONNECTION 时在前提检查前分配临时目录。将共享的地址前提检查移至资源分配前，缺失/异常地址即拒绝，补充无目录分配回归；本次早期残留已按确切绝对路径和 owner 验证清理。 |
| 测试与说明 | `test_release_candidate.py:739`、`test_project_source.py:104`；`tools/complete_smoke.py:1`、`tools/bootstrap_smoke.py:115`、`tools/service_smoke.py:109`；README 的 complete 跨重启与人工核对节、CHANGELOG。 |

### 跨重启判定与兼容性

complete 的程序目录/清单/必要模块/逐文件哈希/版本、两处 owner、launcher、systemd 单元字节/FragmentPath/drop-in 和 source.json 验证保持。通过后只 unlink 活动 program-pending.json 并 fsync，不执行候选程序、自检、重装、备份、证书签发或 systemd 变更命令。

- 同一 boot_id，或任一 boot_id 不可读：两项 timer 状态及新日志的持久/runtime 链接必须与提交快照完全一致。缺失证据不能被当作“已重启”。
- boot_id 变化：持久启用链接的路径/目标/device/inode/mtime/ctime 必须完全未变，当前 runtime 链接必须为空。enabled 只接受标准且未变的 timers.target.wants 依赖，保持 enabled+active；enabled-runtime 和 disabled 仅接受 disabled+inactive，不建立永久启用链接。
- 另核对 systemd ActiveEnterTimestampMonotonic / InactiveEnterTimestampMonotonic：timers.target 必须有本次首次激活时间且无停止时间；enabled timer 须首次激活不晚于该 target、无停止时间；未启用 timer 两个时间须为零。这样可保守拒绝可观察到的手动 start/stop/restart、target 重启、disable/re-enable 链接替换或证据缺失。
- 判定依据已核对 [systemd timer 官方默认依赖](https://github.com/systemd/systemd/blob/main/man/systemd.timer.xml) 和 [systemctl 官方语义](https://github.com/systemd/systemd/blob/main/man/systemctl.xml)。这些属性不是完整系统审计：被清除的历史、非标准启动顺序或文件系统身份变化可能不能自动判断，必须保留日志并人工核对。相同最终状态不证明从未有人操作；最终化也不回写用户策略。
- RC2 合法无子版本 installing 日志仍按原逻辑保留证据后迁移；子版本 1/2 的未完成和 complete 日志保持兼容。无 boot_id 的旧 complete 仅严格比较，不猜测历史。子版本 3 在 complete 必须有 checked 阶段和合法提交证据；旧管理器不理解版本 3 时明确拒绝。

无法自动判断时，README 提供完整人工步骤：私有保留日志、核对当前 timer/开机日志与链接、确认保留当前策略、计算并手工核对该日志 SHA-256，再用可信源码执行 `finalize-program --review-timer <SHA256>`。该入口只接受合法 complete 和精确日志摘要，仅确认 timer 差异；仍验证全部程序/owner/来源/单元及受支持当前 timer 状态，不修改原日志、不初始化目录、不改变服务。程序损坏、未知 schema、未完成事务和过时摘要不能通过；不会建议直接删除 program-pending.json。

### 六状态结果与事务隔离

以下是**Mock 故障注入的跨重启模型**，不代表做过整机重启，也不保证所有发行版必然呈现该状态。每个场景都断言程序文件、inode/mtime、current、备份和服务变更命令保持约束。

| complete 时 | 模型重启后 | 结果 |
|---|---|---|
| enabled + active | enabled + active | 通过，只删除活动日志 |
| enabled + inactive | enabled + active | 通过，不停止 timer |
| enabled-runtime + active | disabled + inactive | 通过，不建立持久链接、不 start |
| enabled-runtime + inactive | disabled + inactive | 通过，不 enable |
| disabled + active | disabled + inactive | 通过，不 start |
| disabled + inactive | disabled + inactive | 通过，不变更策略 |

反例覆盖同 boot 状态/链接变化、跨 boot 单元/drop-in/来源/current/哈希/owner/launcher 异常、用户停用/停止/重启/重新启用、target 重启、时间证据缺失、旧子版本和不可读 boot_id、损坏提交快照、unlink 错误及重复恢复。程序安全校验不因 boot_id 变化而放松；失败日志保持原字节。

绑定隔离覆盖：正常绑定；program complete 但仍活动；未完成卸载/内核/节点修订/节点删除/接管/归还/临时凭据；prepared 接管；损坏 JSON/终态记录/接管记录、未知 schema/phase、外部链接目录/文件；全部在联网前拒绝。合法卸载 complete/rolled_back，及接管 active/returned/rolled_back 可放行；backup、不可变旧修订和 program-pending-rc2.json 审计记录不阻塞。接管 active 表示已经稳定接管，不能把字面的 active 误判为未完成事务。

使用真实 fork 的两个进程争用同一 flock：竞争者不联网、不迁移 legacy owner、不覆盖 source；持锁者完成后仅发布自己的绑定。网络/元数据/写入失败、source symlink 和联网期间外部变更均保留应保留的原字节/外部修改及日志，不为绑定运行卸载/删除/接管恢复。只读入口的缺失状态、缺失锁、符号链接锁和硬链接锁均拒绝，不触碰外部文件。节点监听/PID/配置/分享文本的真实不变性由 service smoke 另行检查（Release API 使用夹具，未冒充真实远端绑定）。

### 最终验证结果

当前单元测试 **198 项通过，0 failure/error/skip**，比基线现有 179 项增加 19 个方法（子场景另列），既有三协议/证书/Token/回滚/接管等回归保留。附加覆盖绑定 replace 后报告错误的旧字节/原缺失回退、回退 I/O 失败和外部编辑/链接保护，以及测试前提缺失不分配目录。

真实 smoke 最终 **19 条工具命令全部退出 0**，包括：

- 六个旧基线（v0.3、RC1、RC2、RC3、来源区分前 RC4、基准 791af46 的来源子版本 2）× intent/current/checked，18 组安装器恢复；最后仅影响绑定的写入错误回退完成后，又针对最终候选追加基准版的 3 组，共 **21 组**。其中 7 组 complete 仅允许只读 systemctl show，并核对文件 inode/mtime/备份不变；两个旧 RC4 管理器实际拒绝事务版本 3。
- `program_smoke`：两个旧基线 × 六状态 × 成功/失败/中断，共 **36 组真实 systemd timer 矩阵**，加首次安装成功/失败 **2 组**。
- `complete_smoke`：六状态各严格只读最终化/人为改动后的 SHA-256 确认恢复，共 **12 组**；真实 systemd，但没有重启系统。
- 安装、旧 v0.3 安装、stdin 管道/TTY/无效输入、同包升级、源码来源双向确认/拒绝、旧三节点 metadata/URI/配置/监听、服务失败回滚/删除隔离通过。绑定成功/拒绝时两个存活节点 MainPID、配置、URI、监听保持不变；Release API 为夹具。
- 本机 Caddy IPv4、IPv6、双栈高端口 HTTP/PID；双自建目标、真实启动失败后的 Caddy/元数据回滚、删除隔离、证书备份恢复及 SNI/端口/证书修改通过。
- stability：三协议原生服务及本机 Reality HTTPS；签发失败夹具保留旧修订/监听；真实部分卸载失败回滚、finalizing 中断续做、清理通过。adoption：两个独立测试服务、三个协议的失败回退、中断恢复、接管及归还通过，原文件和启用策略保留，随后清理。

timer/锁修复先冻结执行矩阵，最后的绑定写入错误回退另跑完整单元及实际来源/服务流程、最终候选追加恢复检查。此前中间批次因修改源码导致候选摘要断言而中止，清理后重跑，不计为最终通过。VPS 专用工具初次因缺失 SSH_CONNECTION 失败；已清理确切残留，修复资源分配前检查，再以 `SSH_CONNECTION='127.0.0.1 12345 8.8.8.8 22'` 作为**分享地址元数据夹具**运行本机测试。不是实有 SSH/VPS 地址，不代表向 8.8.8.8 发起节点联通测试；公网验证仍未完成。

本轮命令（所有系统级工具仅在已核对为空的 Debian 13 amd64 WSL）：

```bash
python3 -m unittest discover -s tests -v
bash -n install.sh
.test-tools/shellcheck/extracted/usr/bin/shellcheck install.sh
python3 nodepilot self-check
python3 tools/native_check.py --download
python3 tools/mihomo_check.py --download
python3 tools/bootstrap_smoke.py --disposable --legacy-source .test-tools/legacy-source --legacy-source .test-tools/rc1-source --legacy-source .test-tools/rc2-source --legacy-source .test-tools/rc3-source --legacy-source .test-tools/rc4-before-source --legacy-source .test-tools/rc4-source-v2
python3 tools/program_smoke.py --disposable --legacy-source .test-tools/legacy-source --legacy-source .test-tools/rc3-source
python3 tools/complete_smoke.py --disposable --legacy-source .test-tools/legacy-source
python3 tools/install_smoke.py
python3 tools/install_smoke.py --legacy-source .test-tools/legacy-source
python3 tools/source_smoke.py --disposable
python3 tools/upgrade_smoke.py --legacy-app .test-tools/legacy-source
python3 tools/service_smoke.py
python3 tools/http_family_smoke.py
# 下列工具在本地使用上文明确声明的 SSH_CONNECTION 元数据夹具。
python3 tools/caddy_smoke.py setup
python3 tools/caddy_smoke.py cleanup
python3 tools/stability_smoke.py setup
python3 tools/stability_smoke.py uninstall
python3 tools/stability_smoke.py cleanup
python3 tools/adoption_smoke.py setup
python3 tools/adoption_smoke.py adopt
python3 tools/adoption_smoke.py return
python3 tools/adoption_smoke.py cleanup
python3 tools/bootstrap_smoke.py --disposable --legacy-source .test-tools/rc4-source-v2
```

最终只读清理核验：NodePilot 安装/命令/续期单元和两种启用链接全部不存在；本轮 `/var/lib` 临时 Caddy/stability/adoption 目录无残留，systemd 无加载的 NodePilot 单元。测试缓存/原始日志仅在忽略目录保留，不上传。未停止或改写既有外部 Xray/sing-box/Caddy，也未改动全局防火墙、路由、IPv6、swap 或 sysctl。

本轮官方 Xray 26.3.27、sing-box 1.14.3、Caddy 2.11.7 下载状态/官方 SHA-256/ELF/报告版本及 8 项原生配置检查通过；Mihomo 1.19.32 三协议导出原生解析通过。bash -n、ShellCheck 0.10.0、自检、Python 编译与 diff 空白检查通过。下载/解析不是客户端真实连接测试。

自检进程在 Debian WSL 的一次观测为 0.214 秒、峰值 RSS 22596 KiB；这仅是短时自检进程，不能替代 1c/256MiB 整机与 ACME 峰值证明。实现只用标准库、短时 systemctl show、有界无跟随目录遍历；不新增依赖或常驻进程。

### Review、外部缺口与发布判断

聚焦审查覆盖：事务版本/阶段拒绝、complete 和 unlink 最后边界、完整候选验证、错误不冒充成功、回滚失败保留、只读入口、锁竞争/owner、timer 启用与运行的独立性、source 的前后校验/replace 后错误，以及外部服务和私有凭据边界。发现的四个附加 P2（含测试工具前提检查）已最小修复并回归。本轮范围暂未发现新的未修复明确 P0/P1/P2 代码缺陷；这不是对整个项目无缺陷的保证。持续磁盘 I/O 故障无法承诺回退落盘，必须报告未确认并人工核对，不能虚构文件恢复。

**完整虚拟机重启：未完成；真实公网 VPS 重启/联通：未完成。** 当前没有指定授权可销毁 VPS/虚拟机和测试域名/Cloudflare Token，不访问生产主机、不终止其他 WSL 分发或代用其他项目凭据。真实 WSL systemd、Mock boot_id 和 SystemExit 中断明确区分；不会写成掉电/整机重启成功。

剩余发布验收：真实 Cloudflare DNS-01 TXT 创建/传播/故障清理、Staging/正式签发及自动续期/插件脱敏，部分公网 IPv6，Ubuntu/arm64 及发行版全矩阵，1c/256MiB 低内存整机和长期负载，客户端 GUI，以及重启期间真实节点监听/证书续期行为。上述结果不能由 Mock/原生配置解析/WSL 代替。

**建议现在冻结代码并进入正式发布验收**；保留 RC4 草稿，不建议直接合并或发布稳定版。最终本地候选包 SHA-256：`320bd03dcfbab066f1c38e837b13f2092d37cbb1d0910179073209d2eec3775b`，Windows/Linux 可复现一致，安装器重新绑定同一摘要。dist、测试内核、凭据、运行日志均不上传；默认 v0.4.0 Release 附件尚未发布，不把本地流程等同于公网安装验收。

## RC4 非 complete 跨启动恢复（历史结论）

2026-10-16，开始前 fetch 并核对远端分支及现有草稿 PR #1，均为审查基线 `587de90e5aa3a8cbe1798f55385dd57643467bd3`，问题仍存在。仅在原开发分支修复 P1；不创建 RC5、新 PR 或 Release，不合并 main。以下行号对应本轮最终源码，前文是历史结果。

### 根因、变更及兼容性

原 `recover_program()` 与 `rollback_program()` 都将事务开始时的 `previous.timer` 直接交给 `restore_timer()`；非 complete 没有可用于跨启动判定的启用链接证据。因此 enabled-runtime 会被重新建立，disabled+active 会被重新启动，enabled+inactive 会覆盖新启动后的正常 active 状态。

| 文件/关键行 | 修复 |
|---|---|
| `nodepilot_pkg/deploy.py:63,85,164,172` | 新事务保存 timer_context：boot_id、启用/运行状态、持久/runtime 链接身份及恢复模式。同 boot 保留原 RC4 语义；跨 boot 复用已有只读证据判定，通过后持久化新启动观察到的 preserve 策略。 |
| `nodepilot_pkg/deploy.py:202,211,218,265` | 成功和回滚共用策略；保存 timer 操作意图/完成点，拒绝阶段与 current、owner、launcher、单元、来源矛盾；迁移或重定策略前保留原日志精确字节的 SHA-256 命名私有审计副本。 |
| `nodepilot_pkg/deploy.py:439,486` | 回滚增加持久检查点，失败保留 rolling_back；preparing 不执行未完成候选。人工恢复仅接受精确日志摘要，确认继续既定事务并保留当前 timer，不豁免候选哈希、路径、所有权、来源和阶段校验。 |
| `nodepilot_pkg/installation.py:81,95` | 复用现有 timer/link/boot 工具，补充真正缺失 timer 的观察与标准自有启用拓扑核对。masked、转换状态、别名/外部链接拒绝。 |
| `tests/test_release_candidate.py:928`、`tests/test_project_source.py` | 新增非 complete 启动周期、阶段、失败重试、首次安装及旧日志人工确认矩阵；调整旧测试的真实模拟 runtime 目录和新事务版本断言。 |
| `tools/recovery_smoke.py:1`、`tools/program_smoke.py:64`、`tools/bootstrap_smoke.py` | 新增明确标注模拟启动证据的原生 systemd 测试；测试清理先完整校验全部自有审计文件再清理；旧 RC4 实际拒绝新子版本。生产卸载仍保留审计证据。 |
| `README.md:147,165`、`CHANGELOG.md`、本报告 | 记录新旧语义、首次安装例外、经过原生测试的人工步骤和验收边界。安装器同步绑定新候选摘要。 |

新事务子版本为 **4**，outer schema/owner 仍为 2，产品仍是 v0.4.0 RC4。版本 3 只有 complete 的提交链接证据；若仅增加可忽略字段，旧 RC4 管理器仍会接受并重放旧 timer。子版本 4 使不理解新语义的旧管理器安全拒绝，未修改节点/证书数据格式。

合法 RC2 日志仍保留原证据、观察当前策略后恢复，因为它没有旧 timer 快照可重放；不会把观察时 boot_id 冒充事务开始 boot_id。子版本 1/2 缺失历史 boot_id、子版本 3 跨 boot 缺失原链接时保留日志等待人工核对；版本 3 同 boot 可按原语义迁移。各版本 complete 及既有 `finalize-program` 保持只读最终化规则，版本 4 还要求提交证据与已确认 context 一致。旧安装器活动日志保护和可信源码 `--local --upgrade` 路径均回归；旧 v0.3 直接入口不能追溯加固。

### 阶段及六状态结果

| 阶段 | 同 boot | 跨 boot |
|---|---|---|
| preparing / intent | 回滚准备工作，不执行未完成复制候选 | 验证文件与启动证据后回滚；不重放上一启动的 timer |
| installing / candidate 至 timer | 验证候选及已有完成步骤，保留原启用/运行语义 | 先证明变化符合启动策略，再保存 preserve 并继续；不启用/启动/停止既有 timer |
| rolling_back / 各恢复检查点 | 还原 current、launcher、owner、单元及 source；失败留日志 | 同样还原文件，但保留本次启动策略；再次失败仍可重试 |
| complete / checked | 原只读最终化及人工入口通过 | 原链接身份/时间判定继续有效；不重装、不备份、不写 systemd |

以下是**模拟跨启动模型**：单元测试模拟 boot/systemd，混合 smoke 模拟 boot ID 和单调时钟属性、操作真实 systemd 状态/链接；没有真实重启。不是所有发行版必然呈现的结果。

| 事务开始时 | 模型新启动后 | 恢复及失败回滚 |
|---|---|---|
| enabled + active | enabled + active | 保留，不额外 start/stop/restart |
| enabled + inactive | enabled + active | 不恢复旧 inactive，不 stop |
| enabled-runtime + active | disabled + inactive | 不重新临时启用，不 start |
| enabled-runtime + inactive | disabled + inactive | 不重新临时启用 |
| disabled + active | disabled + inactive | 不重放手动 start |
| disabled + inactive | disabled + inactive | 保持禁用/停止 |

跨 boot 不只比较 boot_id：要求原持久链接身份未变、runtime 链接消失、当前状态符合策略，并验证 timer/timers.target 的本次原始激活/停止时间。可观察外部 stop、重启、重新建链接、mask、转换状态或证据缺失均留原日志，不覆盖用户操作。这些属性不能代替完整系统审计；被清除的历史、非标准启动顺序或身份变化必须人工诊断。

首次安装独立处理：没有旧 timer 时完成原授权的默认 enabled+active；失败回滚仅停止/移除本次安装的自有 timer 和实际启用链接，避免无主链接。enable 已执行但其证据未落盘就重启时，持久链接变化不能自动归因，必须人工确认。已有版本升级的正常跨启动恢复/回滚不发 timer 生命周期变更命令。

人工步骤见 README：私有保存原活动日志、核对当前状态/链接/日志，明确同意继续既定升级或回滚并保留当前策略，再把精确 SHA-256 交给可信源码中的既有恢复函数；Python `-I` 隔离导入、现有全局锁及 existing_only 均保留。未知 schema、坏哈希/来源/单元/步骤、外部链接仍拒绝。complete 必须走原 `finalize-program`；不能直接删除活动日志。审计副本是历史证据，不是活动事务，也不阻止正常菜单。

### 本轮测试与安全核验

**212 项单元测试通过，0 failure/error/skip**，比 198 项基线新增 14 个方法。新增方法包含六状态 × 16 边界 × 相同/不同 boot 共 **192 个子场景**（含 checked complete）；15 个非 complete 边界各测 boot 不可读/缺失、context/step 损坏，以及外部 stop/链接替换/mask/转换/时间异常；各阶段再次失败与重试、重复恢复/再次模拟重启、跨启动前向失败回滚、原 source 精确字节还原、首次安装各步骤/无主链接、v1/2/3 精确摘要人工迁移及不能绕过的安全检查。

**11 条 smoke 命令全部退出 0**，系统级操作只在已确认无 NodePilot 的可销毁 Debian 13 amd64 WSL，彼此串行：

| 工具 | 本轮结果与证据边界 |
|---|---|
| recovery_smoke | v0.3 与基准 587de90 两个旧版本 × 六状态 × intent/reloaded/rollback-launcher，共 **36 组**。真实文件/链接/systemd 状态，模拟 boot ID 和四项单调时钟值；产品恢复不发 timer 生命周期命令，重复恢复无操作。 |
| recovery_smoke --manual-only | 两个旧版本 × installing/rolling_back，共 **4 组**。真实运行 README 的隔离 Python/精确摘要人工命令；缺历史 boot 的自动恢复拒绝且原日志字节不变，人工后保留当前策略。 |
| program_smoke | 两个旧版本 × 六状态 × 成功/失败/中断，共 **36 组真实同 boot systemd**，首次安装成功/失败另 **2 组**。 |
| bootstrap_smoke | v0.3/RC3/587de90 三基线 × intent/current/checked，共 **9 组**；旧 RC4 实际拒绝 v4，完整安装器 PTY 恢复与重复执行通过。 |
| complete_smoke | 六状态严格只读/人工确认共 **12 组真实 systemd**；没有重启。 |
| install_smoke（2 条）/ source_smoke / upgrade_smoke | 安装、旧 v0.3、stdin/TTY、重复升级、来源确认与拒绝、旧三节点配置/URI/metadata/监听通过。 |
| service_smoke | 三协议真实服务、失败回滚与单节点删除隔离；绑定成功/拒绝时节点 PID、配置、URI、监听不变（Release API 使用夹具）。 |
| http_family_smoke | 本地高端口 Caddy IPv4/IPv6/双栈 HTTP 与 PID 所有权通过，不代表公网 IPv6/HTTP-01。 |

主要可复现命令（旧基线是从可信 Git 修订归档的忽略目录，不上传）：

```bash
python3 -m unittest discover -s tests -v
bash -n install.sh
.test-tools/shellcheck/extracted/usr/bin/shellcheck install.sh
python3 nodepilot self-check
python3 tools/native_check.py --download
python3 tools/mihomo_check.py --download
python3 tools/recovery_smoke.py --disposable --legacy-source .test-tools/legacy-source --legacy-source .test-tools/rc4-source-v3
python3 tools/recovery_smoke.py --disposable --manual-only --legacy-source .test-tools/legacy-source --legacy-source .test-tools/rc4-source-v3
python3 tools/program_smoke.py --disposable --legacy-source .test-tools/legacy-source --legacy-source .test-tools/rc4-source-v3
python3 tools/bootstrap_smoke.py --disposable --legacy-source .test-tools/legacy-source --legacy-source .test-tools/rc3-source --legacy-source .test-tools/rc4-source-v3
python3 tools/complete_smoke.py --disposable --legacy-source .test-tools/legacy-source
python3 tools/install_smoke.py
python3 tools/install_smoke.py --legacy-source .test-tools/legacy-source
python3 tools/source_smoke.py --disposable
python3 tools/upgrade_smoke.py --legacy-app .test-tools/legacy-source
python3 tools/service_smoke.py
python3 tools/http_family_smoke.py
```

中间失败如实保留：旧断言预期子版本 3、旧模拟 runtime 链接目录未接入新预检，调整测试后全量通过。首次混合 smoke 的首个产品恢复已成功，但旧测试清理误将有意保留的审计副本当作未清空；改为测试专用的路径/owner/普通文件/大小/摘要/schema 全量预检后清理，再从头跑完全部矩阵。生产卸载未改变，最终批次无失败。

Xray **26.3.27**、sing-box **1.14.3**、Caddy **2.11.7** 的下载校验/ELF/版本和 **8 项原生配置检查**通过；Mihomo **1.19.32** 三协议导出原生解析通过。ShellCheck **0.10.0**、bash 语法、自检、Python 编译及 diff 检查通过。服务 smoke 观测三协议合计空闲 RSS **124040 KiB**，这是 WSL 上的进程观测，不是 1c/256MiB 整机性能验收。本次只增加标准库短时检查和有界日志/目录观察，无新依赖/常驻进程。

最终清理核验通过：安装目录、状态目录、命令、续期单元、持久和 runtime 启用链接均不存在，systemd 无加载的 NodePilot 单元，`/var/lib/nodepilot-*-test-*` 无残留。Windows/Linux 可复现打包摘要一致，源安装器与 dist 安装器逐字节一致；仅管理入口和标准库模块进入 payload，私有日志、内核与测试目录不进入包。

聚焦审查确认：活动日志最后删除，回滚失败保留；严格所有权/路径/来源/候选验证；管理入口沿用现有全局 flock，不需修改 manager；prepare 不执行半复制候选；complete 只读规则未变；恢复不操作外部 Xray/sing-box/Caddy，也不为 timer 重启代理节点。首次安装失败的自有 timer 清理与既有 timer 策略分开。无全局防火墙、路由、IPv6、swap、sysctl 变更。该范围未发现新增未修复的明确 P0/P1/P2；不能据此宣称全项目没有缺陷。

### 发布判断与未完成验收

**真实虚拟机整机 reboot：未执行；公网 VPS reboot：未执行。** Mock boot ID/时间、单元测试和 WSL 真实 systemd 分别陈述，没有授权可销毁真实重启环境，不将上述结果写成整机重启成功。

仍未完成：真实 Cloudflare DNS-01 创建/传播/故障清理、Staging/正式签发与自动续期、部分公网 IPv6、Ubuntu/arm64 及发行版矩阵、1c/256MiB 整机/长期负载、客户端 GUI，以及真实重启时节点监听与续期行为。前轮 Caddy/stability/adoption 完整工具证据仍为历史，本轮没有把未重跑的历史结果算入 11 条命令。

**建议冻结功能和本轮修复代码，进入 v0.4.0 最终发布验收；保留 RC4 草稿，尚不建议正式 Release。** 本地候选 SHA-256：`22e073c8622e39f2868c2fb4eb3fdb9ff546805e6515bf673ece0fff19c33573`。产物、测试内核及原始日志仅在忽略目录，不上传凭据或运行数据；公网 Release 附件仍未发布。

## RC4 Reality 检测与便携恢复稳定性（当前结论）

2026-10-16，开始执行 fetch，确认本地、远端分支及 PR #1 都为 `42d4bbf8ec260a0d476341a97f5eb3ccab01dd1d`，工作区干净。在现有 `codex/v0.4.0-stability` 修复，不合并 main，不创建 RC5/新 PR/正式 Release，PR 继续 Draft。下列位置对应本轮源码。

### 问题、修复与代码位置

| 等级/位置 | 根因 | 本轮行为 |
|---|---|---|
| P2 `targets.py:306,313,360` | 复查以客户端 SNI 解析结果与固定目标 IP 比较；target 与 SNI 可以不同，旧记录没有原始目标证据。 | 只有匹配当前 target/SNI/端口的 selection.requested_target 域名用于漂移比较。原始 IP 不适用；缺失、失配、未固定或 DNS 失败未知。多 A/AAAA 使用地址值成员判断，兼容 IPv6 不同写法；固定目标、节点和服务不变。身份包含选站记录，避免复用不匹配诊断。 |
| P2 `targets.py:88,93,133,217,283`、`menu.py:316` | HTTP 失败被当作警告继续，但顶层 cf_screened 始终 True；自动排名未检查验证范围。 | 分项记录官方网段、系统可见 DNS/规范名、所选 IP 响应头、TLS 条件；HTTP 未验证则 cf_screened=False，自动排名排除，优先完整检查地址。CF 特征在任一已测地址出现就排除整候选；保留默认候选排除策略。 |
| P2 `manager.py:512,568,571`、`certificates.py:147,172,180`、`menu.py:77,476` | 逐个应用备份，后续节点损坏时前面的服务可能已启动；ACME 转导入、移除 DNS 引用仅有通用提示。adoption 标记可能指向目标机不存在的记录。 | 全体待恢复节点预检后展示数量/协议/原与恢复证书模式，按预览 SHA-256 重新验证再应用。持久化续期关闭提醒，不恢复授权/Token。含 adoption 节点提前拒绝，不导入历史外部控制权。失败清理逐项尝试，清理再次失败报告剩余 ID 与保留证据。 |
| 附加 P2 `services.py:36` | 同名单元 FragmentPath 查询失败被转换为空，可能在无法确认 systemd 状态时继续安装。 | 查询失败直接拒绝，未写服务/未 reload；正常缺失单元仍允许安装。 |
| 附加 P2 `manager.py:470` | 内核恢复切换前未一次性预检全部受影响单元，可能在后一个外改服务上失败前已切核/重启其他节点；Caddy 旧配置损坏也到切核后才发现。 | 先检查全部服务 owner/drop-in、旧目录和 Caddy 证据编码/JSON，再切核/重启；拒绝缺失、链接、越界、未知 schema 与无旧内核却有节点的记录。失败保留原 journal；原子切换/节点回滚机制复用。 |
| 附加 P2 `services.py:36,88` | 最后真实 smoke 出现 AnyTLS clean active/running 但首个轮询尚无监听，原立即回滚。磁盘单元自有也未单独核对已加载 FragmentPath。 | 在任何服务操作前核对实际加载路径；首次就绪允许最多 8 个 0.5 秒轮询（不含 systemctl 调用耗时），随后仍需 8 次稳定 PID/监听。重启/退出/异常状态或稳定期丢监听不获宽限，超限失败回滚。 |

CF 范围严格限定为 [当前官方公开 IP 段](https://www.cloudflare.com/ips/)、系统 getaddrinfo 的 A/AAAA/可见规范名与所选 IP 头部；[getaddrinfo 的 AI_CANONNAME](https://docs.python.org/3/library/socket.html#socket.getaddrinfo) 不是完整 CNAME 链审计，也不证明完全没有 CF。所有 IP/CNAME/HTTP/TLS 项分开呈现。`feasible` 只表示 TLS 条件满足，`cf_screened` 只表示声明范围已检查，未测/失败 IP 单独列出，不能推广到所有地址、未来 DNS 或长期客户端联通。

HTTP 301/302 记录未跟随跳转，403/404 等是网页质量提示，仍可检查有效最终头部；HTTP 超时、拒绝连接、地址族不可达、证书错误、TLS 错误及无效/未完成响应分别分类。解析拒绝不完整 1xx 或坏响应，合并重复同名头，避免后一个 Server 隐藏前面的 Cloudflare 特征。TLS1.3/X25519/h2/可信链与 SNI 验证保持，不因网页状态码放宽。

### 备份恢复与兼容性

恢复仍要求空节点集合，并先拒绝活动/未知事务和外部同名单元。全体待恢复节点先检查元数据、ID/端口冲突、证书 base64/大小/有效期/SAN/私钥配对、各协议内核原生配置、自建目标及 Caddy 聚合配置，再修改服务。预览与应用之间按文件 SHA-256 检测备份变化；应用前再次完整校验，避免把预览当授权后无条件信任。预览可以创建/清理本项目私有临时验证文件，不启动/停止服务。

ACME HTTP-01/DNS-01 和 Caddy acme/dns01 转 import 后显示 **自动续期已关闭，需要重新配置**。节点 restoration 记录保存原证书模式及关闭提醒，菜单列表和证书详情可继续查看；显式重新配置 ACME 后以当前模式为准，不持续显示过时关闭状态。调用既有 dns_credentials.profiles 检查恢复结果无凭据引用，复用凭据安全边界，不为本轮改动 Token 存储实现。备份及原 Token 文件保持，既不复制也不重绑定。导入/自签名也使用 import，客户端信任要求不会因此消失。

兼容 schema=1 普通旧备份，包括没有 adoptions 字段的 v0.3 格式；协议、配置与分享编码不变。选站 checks/restoration 都是增量元数据，不升级节点或程序事务 schema（程序仍 v4），旧选站单一布尔值显示范围未知、建议复查。带 adoption 标记的便携恢复现在明确拒绝，这是有意的安全收紧：不能自动迁移外部服务控制权，不能剥掉标记后保留仅接管允许的弱认证/旧 TLS 行为。应保留原机私有记录并核对/归还，或在目标机重新显式导入；原接管/归还能力不变。

整包恢复不是一个新的原子事务；复用每节点 durable pending/回滚。中途失败撤回本轮已恢复节点，若撤回也失败继续尝试其他项，报告剩余 ID；保留节点修订、相关事务和原备份供人工核对，不报全包完成。已有节点集合一开始就拒绝覆盖。程序升级、complete 只读最终化、timer 启用/运行独立策略与跨启动语义没有重新实现，仅回归。

### 单元、传输与原生检查

**236 项单元测试通过，0 failure/error/skip**，基线 212 项，本轮新增 **24 个方法**，没有把子场景或重复导入 TestCase 计入方法数。

- `tests/test_targets.py`：8 个新增方法，覆盖原 target 与 SNI 分离、多 A/AAAA/IPv6 等价、IP/旧记录/DNS 失败、CF 头、HTTP 异常分类、未完整检查排除、较快未知 IP 不覆盖较慢完整 IP、IPv6 不可达、坏/未完成响应与重复头。
- `tests/test_restore.py`：8 个方法，包含全包后节点证书/编码/密钥/内核/metadata/ID/端口/adoption/缺文件/schema 故障，凭据不变/续期关闭持久提示、Caddy 两种自动模式、预览摘要变化、旧备份、外部服务、重启与清理再次失败、活动损坏事务和写配置故障。
- `tests/test_manager.py`：4 个方法，覆盖全体服务预检、内核回滚再次失败及幂等重试、损坏/缺旧目录记录、systemd 查询失败不授权安装。
- `tests/test_menu.py`：1 个方法，验证完整预览在确认之前，摘要传到应用；`test_release_candidate.py`：1 个方法，损坏 Caddy 证据在切核前拒绝。既有 DNS/排名/Caddy 夹具按新保守语义调整，FakeCaddy 的非 PEM 夹具仅在该旧测试模拟证书验证，新增恢复测试使用真实 OpenSSL 证书。
- `tests/test_features.py`：2 个方法，验证首次延迟成功、永久无监听超限、重启、PID 改变及稳定后丢监听，以及测试客户端退出/超时/监听归属；已有所有权负例扩展实际加载路径不符时无 stop/文件变更。

最终完整单元命令：`python3 -m unittest discover -s tests -v`，本机 Debian 13 WSL 11.885 秒。Python 编译、`bash -n install.sh`、ShellCheck 0.10.0、自检和 diff 检查通过。Xray 26.3.27、sing-box 1.14.3、Caddy 2.11.7 下载校验/ELF/报告版本及 **8 项原生配置检查**通过；Mihomo 1.19.32 三协议导出解析通过。

新增 `tools/target_smoke.py --local` 的最终 **9 个真实 loopback TLS/HTTP 场景**通过：200/301/302/403/404、CF 头拒绝、无效 HTTP 标未知、SNI/证书不匹配拒绝，以及真实 TCP 拒绝连接独立分类。OpenSSL 和 Python TLS 均使用真实握手，正常验证测试证书；只有 DNS、官方网段是明确夹具。该工具已加入 CI，不宣称真实候选站适配。

新增 `tools/restore_smoke.py --disposable` 已在真实 WSL systemd 验证：后节点损坏证书/不兼容内核夹具在任何 restart 前拒绝；两真实 AnyTLS/HY2 服务恢复为 import 后监听健康、原 URI 相同、Token 字节不变；非空集合重复恢复拒绝且真实 PID/修订不变。ACME 元数据是夹具，没有真实签发。

中间失败记录：旧测试将无证据 DNS 推断/缺 CF 完成字段当成功，已改为匹配真实新契约；FakeCaddy 旧文本 PEM 需显式隔离。新测试一次遗漏 base64 导入、传输工具一次读取错误的状态字段，均已修正并重跑。首次方法统计包含导入的整个 TestCase，已移除重复发现。补证书有效期/SAN 子场景时误与系统安装矩阵并行，单元观察到演练中的实际活动日志/runtime 链接而失败；24 条演练完成并清理后重新串行执行，最终只采用 236 项通过结果。没有放宽生产安全检查来迁就并行测试。

Windows/Linux 可复现包摘要一致，安装器绑定 `f484151fb00c02d98cd2be0bd6cf76cd0c09e7acad42f5e35aef093600eca210`。完整 smoke/清理及提交后的范围审计结果记录如下。

### 集成矩阵与复现命令

固定源码第一批 **24 条工具命令全部退出 0**。收尾核对又将新增的 core-pending 普通文件上限校正为 512KiB，给合法最大 256KiB Caddy 旧配置的 base64 JSON 预留空间；既有精确字节恢复测试使用 210KiB 原配置，验证编码后大于 256KiB 的合法日志仍能恢复。最终还细化 TLS TCP 拒绝连接分类、追加真正 v0.3 程序生成的备份恢复。完整单元和目标传输已针对最终代码重跑；上述增量的最终恢复/安装/Caddy/原生复核另行记录，不把中间通过冒充未经复核的最终代码。

| 工具 | 本轮场景与边界 |
|---|---|
| recovery_smoke / --manual-only | v0.3/42d4bbf 两旧基线，六状态 × intent/reloaded/rollback-launcher 共 **36 组**混合证据场景，人工精确摘要 **4 组**。真实状态/链接，模拟 boot ID/单调时钟，未 reboot。 |
| program_smoke | 两基线 × 六状态 × 成功/失败/中断 **36 组真实同 boot systemd**，首次成功/失败另 2 组。 |
| bootstrap_smoke | v0.3/42d4bbf/RC3 × intent/current/checked **9 组**，旧入口保护与重复恢复/备份不变。 |
| complete_smoke | 六状态各严格只读/人工确认 **12 组**；程序/单元 inode/mtime、备份保持。 |
| install（含旧 v0.3）/ source / upgrade | 单入口、TTY/管道、来源控制、旧三协议 metadata/URI/native/监听兼容；最终另测由真正 v0.3 管理器生成的原始备份，当前全包预览/恢复后 3 真实监听与 URI 一致（第三方 Reality 目标网络筛选是夹具）。 |
| service / http_family | 真实服务重启失败回滚、删除隔离及 bind-source 时真实 PID/配置/URI/监听不变；本地高端口 IPv4/IPv6/双栈 Caddy HTTP。 |
| caddy setup/cleanup | 双自建目标、真实 Xray 端口抢占后的 Caddy/metadata 回滚、单节点删除隔离、两目标证书备份恢复和 SNI/端口/证书修改。 |
| stability setup/uninstall/cleanup | 三协议真实服务、本机 Reality HTTPS；续期网络失败夹具保留旧修订/证书/监听；部分卸载失败还原，以及 finalizing 中断续做。 |
| client_probe（2 条） | sing-box 客户端三协议各 2 × 1MiB，Xray 客户端 Reality 2 × 1MiB，总 **8MiB 本地加密传输**，正常证书验证及内容 SHA-256。 |
| adoption setup/adopt/return/cleanup | 两个临时独立源服务、三个协议，原文件/启用状态、故障恢复、接管及归还通过并清理。没有操作真实外部业务。 |

所有系统级工具仅在已核对为空的可销毁 Debian 13 amd64 WSL 串行。VPS 专用工具的 `SSH_CONNECTION='127.0.0.1 12345 8.8.8.8 22'` 是明确的分享地址元数据夹具，没有向该地址发起节点测试。client_probe 在本轮将私有测试 URI 的服务器临时映射为 127.0.0.1，随后恢复原字节；工具原输出字段 external_encrypted_transfer 不代表本轮经过公网，原始日志首行及本报告均标明 **LOCAL LOOPBACK ONLY**。Release API 部分仍是夹具。正常证书验证没有被关闭。

主要命令（归档旧源码和原始日志只在忽略目录）：

```bash
python3 -m unittest discover -s tests -v
python3 -m compileall -q nodepilot_pkg tests tools
bash -n install.sh
.test-tools/shellcheck/extracted/usr/bin/shellcheck install.sh
python3 nodepilot self-check
python3 tools/native_check.py --download
python3 tools/mihomo_check.py --download
python3 tools/target_smoke.py --local
python3 tools/restore_smoke.py --disposable
python3 tools/recovery_smoke.py --disposable --legacy-source .test-tools/legacy-source --legacy-source .test-tools/rc4-baseline-42d4bbf
python3 tools/recovery_smoke.py --disposable --manual-only --legacy-source .test-tools/legacy-source --legacy-source .test-tools/rc4-baseline-42d4bbf
python3 tools/program_smoke.py --disposable --legacy-source .test-tools/legacy-source --legacy-source .test-tools/rc4-baseline-42d4bbf
python3 tools/bootstrap_smoke.py --disposable --legacy-source .test-tools/legacy-source --legacy-source .test-tools/rc4-baseline-42d4bbf --legacy-source .test-tools/rc3-source
python3 tools/complete_smoke.py --disposable --legacy-source .test-tools/legacy-source
python3 tools/install_smoke.py
python3 tools/install_smoke.py --legacy-source .test-tools/legacy-source
python3 tools/source_smoke.py --disposable
python3 tools/upgrade_smoke.py --legacy-app .test-tools/legacy-source
python3 tools/service_smoke.py
python3 tools/http_family_smoke.py
# 下列工具使用上文显式声明的地址夹具，互相串行。
python3 tools/caddy_smoke.py setup
python3 tools/caddy_smoke.py cleanup
python3 tools/stability_smoke.py setup
# 在私有 clients.json 服务器映射至 127.0.0.1 后，正常校验证书：
python3 tools/client_probe.py --private-dir /var/lib/nodepilot-stability-test-<本次随机ID> --requests 2 --parallel 2
python3 tools/client_probe.py --private-dir /var/lib/nodepilot-stability-test-<本次随机ID> --requests 2 --parallel 2 --protocol reality --reality-core xray
python3 tools/stability_smoke.py uninstall
python3 tools/stability_smoke.py cleanup
python3 tools/adoption_smoke.py setup
python3 tools/adoption_smoke.py adopt
python3 tools/adoption_smoke.py return
python3 tools/adoption_smoke.py cleanup
python3 tools/package.py --repo km-hl/Node-Pilot
python3 tools/privacy_audit.py --staged
python3 tools/release_audit.py
```

实际临时 ID 由工具输出和私有状态记录取得，不复制上述尖括号占位符执行。升级/备份/目标模块最后变更后，完整 236 项与目标 9 场景已通过，最终又执行 upgrade、restore、install、caddy setup/cleanup 和缓存内核原生检查；这些最终命令结果及清理需以下收尾核验为准。

### 风险与正式发布验收

本轮发现的 DNS 漂移、CF 成功语义、恢复预检/提示和三个附加 P2 已修复；未发现本轮范围新增未修复的明确 P0/P1/P2。完整系统审计、全项目无缺陷或整包原子恢复都不是本轮证明。持续磁盘 I/O 故障/回滚再次失败仍需保留证据人工核对，不能承诺总能还原。

补充边界失败如实记录：最终追加服务来源检查后，真实 service_smoke 的 AnyTLS 在 active/running/exit=0/restarts=0 时首轮尚未观察到自有监听，正确撤回该新节点并清理，但过早判断不利于慢启动。新增首次有界就绪等待与 5 种单元子场景；236 项全量重新通过。该服务公共路径修复后，再串行复跑受影响的节点、安装、旧版备份恢复、Caddy、传输、卸载及接管/归还工具，结果单独收尾核验，不将这次失败算作通过。

19 条追加流程的首次客户端传输因本地测试代理尚无监听而中断；原 client_probe 的等待循环耗尽后仍会继续发请求，不能区分客户端退出/未就绪/外部占用。仅修正测试工具为有界进程存活与自有 PID 监听检查，并补充退出/超时/成功单元场景，生产 payload 不因该工具变更而改变。保留首次失败日志，核对剩余测试状态 owner 后续跑：两个客户端、卸载清理及接管归还全部通过，19 项最终成功结果完整。没有降低 TLS、完整性或节点健康要求，也不将首次失败算作通过。

**适合冻结代码并进入授权可销毁 VPS 的最终验收，尚不满足正式 Release 条件。** 三协议、终端管理、版本选择、接管归还、证书/Token 生命周期、脚本来源与显式绑定、complete/timer 恢复保留；没有新协议/依赖/常驻进程，没有全局防火墙、路由、DNS、IPv6、swap、sysctl 操作，没有使用生产凭据或生产主机。

必须补齐的实机验收：

- 真正 VM/VPS reboot/升级故障恢复及续期 timer 启动策略，不能由 Mock boot 或 WSL systemd 替代。
- 授权域名的 Cloudflare DNS-01 TXT 创建/传播/失败清理、Staging/正式签发、自动续期与实际插件脱敏；HTTP-01 的公网 TCP80/云安全组和双栈。
- 公网 IPv4-only/IPv6-only/双栈节点端到端连接及 HY2 UDP/云安全组诊断；本机 IPv6 不能代表公网 IPv6。
- Debian 12/13、Ubuntu 22.04/24.04、amd64/arm64 的真实安装/升级/恢复；CI 的 Ubuntu 单元/原生检查不等于这些 VPS 全流程。
- 已有独立 sing-box/Xray、3x-ui、Caddy/Docker/网站反代及 80/443 占用的真实共存演练；本轮临时独立源服务和拒绝测试不能覆盖所有面板/容器部署。
- 1c/256MiB 整机的安装/更新/ACME 峰值及长期负载；本次 WSL 服务空闲进程 RSS 124512 KiB 不包含整机或 ACME 峰值，不能保证三协议同时稳定运行。
- Mihomo/v2rayN/Egern 等客户端 GUI 导入/正常证书验证及真实公网传输。下载/API 不可用的拒绝/回滚测试保留，但仍需目标网络环境的安装与断网恢复验收。

真实公网 VPS 测试、整机 reboot、真实 DNS-01 签发/自动续期、arm64/低内存整机/GUI：**本轮均未验证**。建议继续保留 PR #1 为 Draft，完成上述条件后再决定合并/正式发布。

### 收尾核验

最终代码的 236 项单元、9 个真实目标传输场景、实际 v0.3 备份升级恢复、备份策略 smoke、新安装、Caddy setup/cleanup 与 8 项缓存内核原生检查均通过。合计 **49 条 smoke 命令成功完成**（固定批次 24 条，增量复测 6 条，服务边界修复后重跑 19 条；原生检查、单元和静态检查另计）。最终批次无 failure/error/skip；中间调试失败及并行测试干扰已在上文说明，不计为最终通过。

清理确认：NodePilot 程序/状态/命令/续期单元及 /etc、/run 启用链接均不存在，无加载的 nodepilot-* 单元，/var/lib/nodepilot-*-test-* 与 /run/nodepilot-restore-test-* 无残留。87 个允许公开的 Git 暂存文件范围/敏感信息审计通过；22 个修改/新增文件均为公开源码、测试、文档和 CI，没有 dist/二进制/配置/Token/原始日志上传。最终候选 Windows/Linux/dist 包字节摘要一致，源与 dist 安装器逐字节一致，SHA-256 为 `f484151fb00c02d98cd2be0bd6cf76cd0c09e7acad42f5e35aef093600eca210`。

本轮修改清单：targets.py、manager.py、certificates.py、menu.py、services.py；test_features.py、test_targets.py、test_restore.py、test_manager.py、test_menu.py、test_caddy.py、test_release_candidate.py；tools/target_smoke.py、restore_smoke.py、upgrade_smoke.py、client_probe.py；install.sh 的摘要绑定、.github/workflows/check.yml 的本地传输步骤；README、CHANGELOG、REALITY_FLOW 和本报告。关键函数/测试位置及复现命令见上文；最终提交 SHA 与远端 PR/CI 核验在交付答复及 PR 中给出。正式 v0.4.0 附件仍未发布。

## RC4 手动选站一致性与最终审查（当前结论）

日期：2026-10-16。开发前 HEAD `184653efcc0618ac8b296e1038bc4e30a5885626`；`git pull --ff-only origin codex/v0.4.0-stability` 确认已同步，PR #1 OPEN/DRAFT、head 相同，无审查评论。阅读 README、CHANGELOG、上述 RC4 记录及既有测试后继续原分支。本轮提交 SHA、推送及 CI 状态见交付答复与同一 PR 的当前说明；不创建新 PR/RC5/正式 Release，不合并 main。

### 修复及兼容边界

| 等级/位置 | 可复现问题与修复 | 兼容性/验证 |
|---|---|---|
| P2 `menu.py:reality_input` | 手动检查返回 IP，却保存原域名，实际运行可能连接未经验证地址。默认改用返回的 target，原输入另存；保留独立 SNI。 | 新建/明确重选才采用新行为。编辑旧第三方节点默认 keep；原 target/SNI/UUID/shortId/密钥不因升级或回车重选。保存仍走已有原生检查/事务确认。 |
| P2 `menu.py`、`targets.py:screening_unknown/rank_targets` | HTTP 失败仍可无额外确认手动使用。现列出未知分项/原因，建议重测，强制采用需两次确认；明确 CF 直接拒绝、TLS 证据不足不能强制通过。 | 自动候选要求分项完成证据，单设 cf_screened=True 不生效。官方 IP 段或 DNS 获取失败仍直接中止，不放宽原校验。 |
| P2 `targets.py:inspect_target/recheck_target/target_status` | 手动记录 target 与运行域名不匹配，动态模式未区分，SNI 修改后历史可能错误关联。新增明确模式/本次 IP/地址族/端口；动态域名须风险确认，复查独立显示 DNS/抽测范围，dns_drift 保持空值；历史还需匹配 SNI。 | 旧记录继续读取，缺证据显示未知，不推测模式。固定 IP 仍按 requested_target 原域名检查漂移，IP 原目标不适用，IPv6 按值比较；只写诊断，不改节点。 |
| P1 `caddy.py:Caddy.assert_owned`、`installation.py:assert_renew_owned` | 磁盘自有文件存在时，Caddy 与卸载/恢复续期单元遗漏实际加载 FragmentPath。外部同名单元可能被停止。现操作前核对加载来源、文件及 drop-in，查询失败也拒绝；卸载两类恢复阶段先全体预检，保留原事务。 | 外部变化会保守拒绝并需人工核对，不修复/覆盖外部服务。正常安装/续期策略/回滚不变。单元故障注入及三个真实 systemd 外部加载单元拒绝验证通过。 |
| P2 验收工具 `vps_smoke.py:setup` | 旧测试池从 Cloudflare/Apple 等选首个 TLS 站点，与正式 CF 检查矛盾，可能在随后创建时失败。现复用 rank_targets 和固定 IP/独立 SNI/selection。 | 两个模拟测试检查选择传递和无合格候选时不创建服务；本轮不在真实 VPS 执行 setup，不声称公网验收已完成。 |

selection 新字段：target_mode=pinned_ip/literal_ip/dynamic_dns、dynamic_dns、original_host、target_port、selected_ip、selected_family、tested_target；既有 requested_target、target、sni、checked_at、addresses/failures/untested_addresses、checks 保留。动态 target 为原域名，本次 tested_target 仍为通过三次 TLS 检测的 IP。手动 IP 输入保留原写法；域名固定只能选已验证结果，失败地址不会进入 results。没有改变节点 schema、服务端协议配置格式或分享认证字段。

字段关系对照 [Xray 官方 REALITY 配置](https://xtls.github.io/config/transports/reality.html)：target 与 serverNames/客户端 serverName 分开，固定 IP 不改客户端 SNI。也核对 [sing-box AnyTLS](https://sing-box.sagernet.org/configuration/inbound/anytls/) 和 [Hysteria2](https://sing-box.sagernet.org/configuration/inbound/hysteria2/) 文档；本轮未改这两种协议生成器，原生检查使用下述具体版本，不宣称所有未来版本兼容。

### 最终测试证据

环境：本机可销毁 Debian 13 amd64 WSL，真实 PID1/systemd；无生产服务器访问。基线 **236** 项通过，最终 **253** 项通过，0 failure/error/skip（新增 17 个方法及其子场景）。覆盖手动域名固定/IP 保留/独立 SNI/高级动态确认及取消、CF 未知二次确认与明确 CF 拒绝、布尔值绕过拒绝、多个 IPv4/IPv6/首地址失败/部分族不可达、原域名 DNS 变化与等价 IPv6、keep/旧记录/新 SNI 不复用历史；既有创建/修改/启动慢/PID 变化/启动失败回滚、升级异常/外部单元、坏证书/旧 ACME/接管备份拒绝/重复恢复测试保留。

| 证据层级 | 本轮实际执行结果 |
|---|---|
| 单元/Mock/故障注入 | 253 项全部通过；网络、CA、Release API、启动周期相关夹具仍是模拟，不能算公网/重启成功。 |
| 原生配置/静态 | Xray 26.3.27、sing-box 1.14.3、Caddy 2.11.7 的缓存官方内核共 **10 项**通过（原 8 项加固定 IPv4/IPv6 target + 独立 SNI）；Mihomo 1.19.32 三协议 YAML 解析通过。编译、自检、bash -n、ShellCheck 0.10.0、git diff --check 通过。 |
| 本地真实 TLS/HTTP | target_smoke 的 **9 场景**通过，含 200/301/302/403/404、CF 标记、头未完成、SNI 错误与 TCP 拒绝；DNS/官方网段是夹具，TLS 证书用测试信任锚，未关闭校验。 |
| 真实 systemd | 主批次 **23 条命令**全部退出 0（其中静态/原生等 8 条，服务/安装/恢复等 15 条）。含三协议监听/失败回滚/删除隔离、两节点坏证书/原生预检与 Token 不变、双 Caddy 编辑和证书恢复、PTY 安装/旧版升级/来源迁移、六策略 × 严格/人工 complete 共 **12 组**、续期失败保留旧证书/修订、卸载中断续做、两个独立源服务接管/归还。 |
| 实际加载外部单元 | 另用三个 /run 临时单元验证 Caddy/renew.service/renew.timer 的真实 FragmentPath，全部拒绝，外部字节和 inactive 状态不变；临时单元清理。 |
| 本地真实代理数据 | 补充 stability setup/cleanup、sing-box 客户端三协议和 Xray Reality 客户端，均正常证书校验，每协议两次、合计每组 2MiB 数据 SHA-256 一致。分享地址夹具映射 127.0.0.1，工具 external 字段不能当作公网证明。自建目标/测试信任证书不代表第三方公网固定 IP 实连。 |
| GitHub Actions | 推送后的精确提交状态以 PR/交付核验为准；不将本地通过当作 CI 成功。CI 原流程含官方重新下载/校验，未删减。 |
| 真实 VPS/ACME/重启/性能 | **本轮未执行**。无授权测试 VPS/域名/Token；未做真实 CA/DNS-01/HTTP-01、公网 IPv6/UDP/云安全组、整机 reboot、arm64/四系统、GUI、1c/256MiB 整机长测。 |

本次未重新执行 native_check/mihomo_check 的 `--download` 分支；使用先前通过官方 SHA-256/ELF/版本校验的具体缓存版本。没有把旧下载成功或历史 49 条 smoke 当成本次重跑，也没有为本轮声称全旧版本/全部 timer 中断阶段复跑。低内存仅延续已有设计限制；本次服务 smoke 空闲 RSS 合计 125228 KiB 不含整机/安装/ACME 峰值，不是 256MiB 可用性证明。

复现主批次命令（只能在已检查为空且明确授权的可销毁 Linux 环境运行，涉及 systemd 的工具串行）：

```bash
python3 -m unittest discover -s tests -v
python3 -m compileall -q nodepilot_pkg tests tools
bash -n install.sh
shellcheck install.sh
python3 nodepilot self-check
python3 tools/native_check.py
python3 tools/mihomo_check.py
python3 tools/target_smoke.py --local
python3 tools/http_family_smoke.py
python3 tools/service_smoke.py
python3 tools/restore_smoke.py --disposable
python3 tools/caddy_smoke.py setup
python3 tools/caddy_smoke.py cleanup
python3 tools/install_smoke.py
python3 tools/upgrade_smoke.py --legacy-app .test-tools/legacy-source
python3 tools/source_smoke.py --disposable
python3 tools/complete_smoke.py --disposable --legacy-source .test-tools/rc3-source
python3 tools/stability_smoke.py setup
python3 tools/stability_smoke.py uninstall
python3 tools/stability_smoke.py cleanup
python3 tools/adoption_smoke.py setup
python3 tools/adoption_smoke.py adopt
python3 tools/adoption_smoke.py return
python3 tools/adoption_smoke.py cleanup
```

最初一个新增 keep 测试将旧行为补全 mode=external 误当成连接参数变更，修正夹具并增加无 mode 旧节点的默认 keep 参数测试后通过。临时调度器初次缺 sys.path 导致工具尚未运行，修正后完整运行。一次 WSL 单元命令权限自动审查超时，按指引重试成功；不是测试失败或安全限制。原始日志、调度器、客户端测试证书/配置及本地包均留忽略目录，不上传。

### 审查范围与发布判断

逐模块检查 manager、deploy、services、targets、certificates、dns_credentials、caddy、adoption、installation、project_update，并对照 model/config/connectivity、相关测试与既有修复。重点复核所有权、锁/事务/回滚、路径和符号链接、证书/Token、3x-ui/独立服务边界、80/443 占用、便携恢复与来源授权。仅修改上表可解释/复现的问题；其余模块保持现有实现，不增加依赖、协议、数据库、Web/常驻服务或大规模重构。Docker 网络、网站反代和生产服务拓扑只做代码边界审查，没有实际共存验收。

本轮发现的 P1/P2 已修复，审查范围内未确认尚未修复的 P0/P1；不能据此保证全项目在所有环境无缺陷。持续 I/O 故障、损坏/外部修改的恢复证据可能仍需人工核对，便携整包恢复不是全包原子事务。旧管理器不具备新事务守卫，须用可信新版恢复。

**建议冻结当前 RC4 代码，进入授权可销毁真实 VPS 最终验收；不建议现在合并 main 或正式发布 v0.4.0。** 发布阻塞验收包括真实 DNS-01/续期/失败清理和实际插件脱敏、真实 reboot/升级异常恢复、公网 IPv6/UDP/云防火墙、系统/arm64 矩阵、3x-ui/Docker/既有网站共存、GUI 客户端，以及 1c/256MiB 安装/ACME/升级峰值和长期负载。详见完善后的 [VPS 验收清单](VPS_ACCEPTANCE.md)，这些是未验证风险，不冒充已复现 P1 缺陷。

最终可复现包（Windows/Linux 一致）SHA-256：`645d116e5dd1ceceb4c7af3e5b372997e9de36010f9f29481e4bb46e5c2990ae`；源码 install.sh 与本地生成安装器绑定同一摘要。默认 v0.4.0 Release 附件仍未发布。临时程序/状态/服务已清理；公开文件范围、敏感信息和提交后 payload 逐文件审计结果随交付核验记录。

## RC4 授权 VPS 补测（当前结论）

2026-10-16，用户明确授权一台 VPS 测试后，完成本轮真正公网验证，详见 [VPS 报告新增节](VPS_TEST_REPORT.md#v040-rc4-实机补充验收2026-10-16)。此前“本轮未访问生产 VPS”的表述保留为上一轮边界，不代表此次授权补测未执行。

- Debian 13 amd64、1 CPU、约 2GiB、IPv4；保留原 x-ui/Xray、Docker 和 NodePilot 0.3.0，使用独立候选源码/状态/临时服务。原安装未升级，原续期 timer 保持 enabled + active。
- 三协议公网传输/HTTPS、Xray 与 sing-box Reality 客户端、5 组错误认证/obfs/SNI 负例、手动固定/明确动态/默认 keep/只写 DNS 诊断、真实 CF 拒绝、端口和凭据修改、启动失败回滚、备份恢复后重连、双 Caddy 目标实连、两个临时独立源服务接管/归还通过。所有阶段累计验证 45MiB 数据摘要。
- 180 秒、1 CPU/256MiB/无 swap 进程组，三协议各 8MiB、最多 4 并发；355 个样本，kernel memory.peak **174272512 字节（166.2MiB）**，OOM/oom_kill 为 0。包括冷 inode、原生检查/重启及测试证书生成，不包括完整 OS、Caddy、安装/升级或真实 ACME，不能抵扣 256MiB 整机长测。
- 三官方内核本次重新下载、SHA-256/ELF/版本及 10 项配置检查通过。实机发现 Mihomo 默认 amd64 附件需 v3 CPU，验证工具改选官方 amd64-v1，新增回归后本地和实机 **254 项完整测试通过，无失败/跳过**；Mihomo 下载/三协议 YAML 解析通过。远端无 ShellCheck，未安装新系统依赖；本地与 CI 分开验证。
- 原 8 个关键文件摘要、原服务状态/MainPID、原 Xray PID 和程序 current 均保持。临时服务/监听/源码/内核/状态已清理，生成的私有备份与原始日志仅留服务器 root 私有 0600 压缩包，不上传公开仓库。候选管理器 payload 未修改，仍绑定上节摘要。

本轮没有发现新的管理器 P0/P1；唯一新增代码修复为测试工具 CPU 附件兼容。建议继续冻结管理器并完成剩余验收，PR #1 保持 Draft。真实 ACME/续期/TXT 清理、真实 reboot、干净安装/原程序升级故障、其他系统/arm64/公网 IPv6、Docker 网站反代、GUI 客户端、256MiB 整机长期负载仍未完成，不建议现在合并 main 或正式发布 v0.4.0。

## RC4 升级与真实 reboot 专项（2026-10-17）

受测管理器仍为 `e4850d1bcca8f8ed483ff6f4f295666b133f9f70`，payload `645d116e5dd1ceceb4c7af3e5b372997e9de36010f9f29481e4bb46e5c2990ae`。用户本轮重新授权原 VPS 为可销毁测试环境；先保存原 NodePilot，再从正式 v0.3.0 Release 安装，三节点真实公网基线通过后按 `--local --upgrade` 升级 RC4。升级保留节点修订/配置/证书/URI/单元及运行 PID，状态 schema=2；没有使用不存在的 v0.4.0 Release。

10 组真实整机 reboot 全部通过：enabled+active、disabled+inactive、enabled-runtime+active、disabled+active、enabled+inactive，以及 intent/reloaded/complete/回滚途中中断与节点 pending。每组实际 reboot、重新 SSH、新 boot ID、实际 socket/所有权和原配置摘要验证，再由独立客户端三协议公网数据/HTTPS 复核。complete 恢复前后程序文件 inode/mtime/摘要相同；合法事务恢复后再执行无 pending。私有注入器保留明确故障标记，没用假 boot ID/时钟替代重启。

真实 404/SHA 不匹配/非 tar 安装包均被安装器拒绝；两种无效候选是明确加载/schema 夹具。官方 Xray 更新后实际端口冲突触发启动失败并回滚；Caddy 未知 app 配置夹具由真实原生 validate 拒绝，保留旧内核/配置。五项拒绝及内核失败后原三协议再次公网成功。本轮合计 42MiB 数据摘要通过；本地 Debian 254 项通过、VPS 最终 254 项通过（38.529秒），远端无 ShellCheck。详见 [分层矩阵、命令和限制](VPS_TEST_REPORT.md#rc4-程序升级与真实重启专项2026-10-17)。

结束通过自有卸载事务清理测试安装后恢复原 v0.3.0/零节点、enabled+active timer，103 个记录文件摘要一致，x-ui/Docker 正常，测试单元/监听清理。外部 PID 因真实 reboot 改变，按文件完整性与状态核对；原始 journal/节点备份/日志保留 root 私有，不上传。

未确认新的管理器 P0/P1，本轮仅更新文档、没有新增行为/依赖/协议，不更改 CHANGELOG。真实 ACME 尚未执行：缺授权域名/Zone/Token，服务器无 Certbot/global IPv6；未发起 DNS 修改或 CA 签发。固定正式 CA 的现有入口不能当作 Staging 开关。真实 CA/续期/TXT 清理/插件脱敏、干净 OS、正式 Release 下载升级、其他系统/arm64/公网 IPv6/GUI、256MiB 整机长测及安装/ACME 峰值仍待验收。**继续保留 PR #1 Draft，不合并或发布。**
