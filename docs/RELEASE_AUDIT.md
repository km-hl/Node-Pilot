# v0.4.0 最终 Release Audit

**后续状态（2026-10-19）：** PR #1 已合并；发行tag `v0.4.0` 固定指向 `6900d17c0127a18c3c876e1b28757e3a5eb17419`。原三附件重建/公开下载/Release Audit一致，[真实在线交付](VPS_TEST_REPORT.md#online-delivery-2026-10-19) 为 **ONLINE DELIVERY PASS**；无新增已确认未修复P0/P1。用户单独授权后，同一 Release ID `416519274` 原地转为 **Stable / Latest**，tag/附件未改，后续main仅更新文档，本轮未访问VPS。完整实测范围仅Debian13 / amd64 / IPv4 / 约2GiB；干净OS依赖安装、正式CA节点在线升级、其他平台/公网IPv6/GUI、256MiB整机与自然临期长期运行仍不作已验收承诺。下文为原审查/分阶段清单，保留各轮当时状态；当前说明见 [Release Notes](RELEASE_NOTES.md)。

日期：2026-10-18；分支 `codex/v0.4.0-stability`，现有 [PR #1](https://github.com/km-hl/Node-Pilot/pull/1)。本轮不访问 VPS、不读取实际 Token/私钥/内部产物，不合并、不改 Draft、不创建 tag/Release 或修改正式渠道。

## 决策与版本

**GO：限定范围的候选代码可以进入最终人工评审与发布准备。** 没有已确认的未修复 P0/P1；本轮修复的是发行门禁，不修改管理器。GUI、其他平台和极低内存不是已验收承诺。正式附件在线安装仍为 NOT TESTED，候选通过不等于已发布或在线交付完成。

- 起始/远端基线：`848dfced41dd1c7c07d04ab06285c306ab2b95a2`；本轮提交只含审计工具、回归、CI和文档，最终完整 HEAD 以 PR head/本轮交付记录为准（避免文档自引用提交摘要）。
- fetch 后 main：`548e84202589dc0be5898a02552541a0065a6de5`；起始分支与 main 差异68文件，8403新增/391删除。通过 `gh pr diff 1` 及 Git 三点 diff 核对同一 PR。
- 起始精确 HEAD 的 [push CI](https://github.com/km-hl/Node-Pilot/actions/runs/39130727980) 与 [PR CI](https://github.com/km-hl/Node-Pilot/actions/runs/39130733327) 均 SUCCESS。最终提交须重新等待 CI，不能沿用这两个运行冒充新增修复已在 CI 通过。
- 目标版本与源码：`0.4.0` / prospective tag `v0.4.0`。正式最新 Release 仍为 v0.3.0，main 在线入口仍是 v0.3.0。
- 候选管理器 SHA-256：`645d116e5dd1ceceb4c7af3e5b372997e9de36010f9f29481e4bb46e5c2990ae`；26个文件，与三轮 VPS 受测 payload 相同。本轮不更换 CA、信任策略、服务、协议配置或恢复语义。

## 代码审查和问题

审查 main→候选差异及 manager/deploy/services/installation/project_update、certificate/DNS runner/Caddy、targets/adoption、install.sh 和打包审计路径。重点核对所有权、FragmentPath/drop-in、候选执行前摘要、归档白名单、旧证书不可变修订、timer双状态、complete只读终结及失败保留。没有以“全测试通过”替代对这些边界的阅读；不保证不存在未知缺陷。

| 等级 | 位置/证据与影响 | 处理与阻断情况 |
|---|---|---|
| P0 | 未确认可复现的秘密泄漏、未校验下载执行、路径穿越或误操作外部服务问题 | 无确定未修复项 |
| P1 | 基线 `tools/release_audit.py:48` 只与工作区 install.sh 比较。把候选安装器和工作区安装器同时追加相同未提交命令，payload不动，原门禁可通过；“与受审查HEAD一致”的结论失真 | 已改与 `git show HEAD:install.sh` 精确字节比较；真实临时 Git 回归覆盖同改副本拒绝。只影响发布工具，无需重跑管理器实机。修复前阻断，修复验证后解除 |
| P2 | 基线 `tools/release_audit.py:22` 未将源码版本、tag和归档文件名绑定，安装器URL/SHA只检查子串 | 已加版本/tag/附件名与唯一字段、完整校验文件检查；回归覆盖错tag、改名、重复绑定、篡改payload。CI新增真实候选审计 |
| P2 | 基线 README:33 无条件称四系统/两架构“支持”；VPS_TEST_REPORT:3 和 RC_FOUR_VALIDATION:3 指向较早“最新节”，可能误读 ACME/reboot缺口 | 已收窄实机承诺、增加支持矩阵和顶部最新链接。历史记录保留日期和当时边界；不阻断收窄后的发布 |
| P2 | GUI原样导入/公网连接尚无证据；核心解析不能推出GUI成功 | [逐客户端手动清单](CLIENT_ACCEPTANCE.md)，全部GUI NOT TESTED；不是已复现配置缺陷，不盲改内核。阻断“GUI兼容已验证”的宣传，不阻断本次限定范围 |

发行工具回归覆盖正常包、同时修改两份安装器、版本/tag错配、附件名错配、更新校验和后的payload篡改、重复URL绑定。发行脚本只写指定本地tar/摘要/安装器，不执行git提交、tag、上传或发布。

## 现有真实证据复核

本轮只核对公开报告、Git历史和payload一致性，未重新读取私有原始日志/receipt或联系VPS。以下为既有实测记录，不是本轮新增实测；其适用管理器字节未变。

| 记录 | 可承接证据 | 不可扩大范围 |
|---|---|---|
| [10月16日](VPS_TEST_REPORT.md#v040-rc4-实机补充验收2026-10-16)，c63106f / 工具修复e4850d1 | 三协议公网及HTTPS、5负例、修改/回滚/备份恢复/删除、Reality手动固定/动态/CF、双Caddy与临时独立服务接管归还；45MiB | 自签测试信任不算正式CA；Dockerdaemon无运行容器不算网站拓扑 |
| [10月17日](VPS_TEST_REPORT.md#rc4-程序升级与真实重启专项2026-10-17)，e4850d1 | 真实v0.3.0 Release新装→可信本地RC4升级；10组真实reboot、timer策略与事务阶段、重复恢复、坏包/下载拒绝及内核回滚；42MiB | 同机全新NodePilot不是干净OS；没有正式v0.4.0附件；故障夹具和真实重启分开 |
| [10月18日](VPS_TEST_REPORT.md#rc4-真实-acme-专项2026-10-18)，d2e86ac | Staging/正式三DNS与双HTTP-01目标；正式8张(5首次+3续期)；9故障保留旧修订；28MiB/HTTPS；Token批准文件与日志/argv扫描 | Staging仅隔离副本换官方Directory/专用根；生产源码未变。Caddy加速窗口自动续期和手动force不等于自然临期长期运行 |
| 资源 | 180秒1CPU/256MiB进程组，355样本，峰值174272512字节=166.2MiB，swap/OOM为0 | 实际主机约2GiB；不含完整OS/Caddy/安装升级ACME峰值；不是256MiB整机稳定保证 |

ACME TXT传播由API观察+20秒固定等待+真实CA验证证明；独立resolver采样没有成功记录，不声称测得精确传播时延。错误TXT注入留下1条，验收器核对专属记录后清理，不能称产品能自动清理任意第三方改动。SIGTERM宽限清理通过不能保证断电/API故障永无残留。原服务保护以各轮103文件等摘要/运行和监听证据为准；真实重启使PID变化，不能说跨reboot PID不变。

## 环境和资源支持矩阵

| 分级 | 环境/功能 | 本次声明 |
|---|---|---|
| 已真实验证 | Debian13 / amd64 / IPv4 / 1CPU / 约2GiB；已有x-ui/Xray、Dockerdaemon；三协议、DNS-01/Caddy HTTP-01、升级/reboot | 本次主要验收范围；不推及任意共存拓扑或持续满载 |
| 自动化验证但未整机验收 | Ubuntu24.04 amd64 GitHub Actions；Debian本地WSL；单元/事务故障、官方原生配置、Mihomo解析、loopback双栈 | 可说明测试通过，不能等同Ubuntu VPS安装/ACME/reboot或公网IPv6 |
| 自动化受限资源验证 | 1CPU、256/512/1024MiB进程组矩阵及180秒公网组负载 | 仅所测进程/时段；不承诺256MiB整机或自然续期长测 |
| 理论支持、未实机验证 | Debian12、Ubuntu22.04/24.04完整VPS、arm64 | 安装器接受/有官方架构选择；实验性路径，扩大正式声明前补测，不改OS白名单掩盖差异 |
| 理论支持、未公网验证 | IPv6-only、双栈公网ACME/协议、其他云网络；复杂Docker网站/反向代理；自然临期长期自动续期 | 可按清单验收，不以本机监听或加速测试抵扣 |
| 当前不支持 | 非root、非systemd、非Debian/Ubuntu列定版本、32位/非amd64/arm64、Windows/macOS作服务端 | 安装器/系统检查拒绝；WSL只作开发验证环境 |
| 当前不实现 | XHTTP/CDN、Trojan/VMess/Shadowsocks、Web面板、多机管理、订阅服务器 | 不在v0.4.0范围 |

操作依赖Python>=3.10、SSH TTY、系统签名源、官方HTTPS下载；HTTP-01需要正确DNS与公网TCP80，Certbot standalone双栈明确拒绝并建议DNS-01/Caddy。DNS-01需要限定Zone的Token和发行版插件，不需TCP80。没有测出可信的最低整机RAM数值；约2GiB是受测规格，不把它写成算法下限，也不编造256/512MiB生产保证。

## 发行、安装和供应链

候选生成命令（本地文件，不上传）：

```bash
python3 tools/package.py --repo km-hl/Node-Pilot --tag v0.4.0
python3 tools/release_audit.py --repo km-hl/Node-Pilot --tag v0.4.0
python3 tools/package.py --archive /tmp/nodepilot-repeat.tar.gz
cmp dist/nodepilot-0.4.0.tar.gz /tmp/nodepilot-repeat.tar.gz
python3 tools/privacy_audit.py
```

归档只含nodepilot及nodepilot_pkg显式路径，26个普通文件，无配置/证书/Token/SSH/备份/测试工具/日志/内核。固定gzip时间戳、顺序、权限和tar时间；不同平台包摘要相同。安装器独立附件保持与Git HEAD精确一致，SHA绑定上述payload，URL只预留未来版本；**这里不提供尚不能使用的v0.4.0在线安装命令**。README中main入口明确仍是v0.3.0。

安装脚本下载HTTPS+SHA后仅提取有界白名单普通文件，拒绝重复/链接/越界路径，再自检和隔离部署。内核官方GitHub API提供SHA后下载，架构/报告版本/原生配置检查；镜像只改传输，不改官方摘要。信任起点仍是操作者选择的HTTPS安装器/源码与上游账户，摘要不等同独立签名或抵御上游账户失陷。

首次安装/TTY、重复运行、来源确认和升级回滚承接既有Bash/PTY/systemd与实机记录；本地安装默认不绑定在线来源，绑定更改需确认。v0.4.0不能安全降回旧管理器；不通过删除失败Release来“回滚”用户节点。撤回发行应停止推广并保留用户数据和事务，按可信新版/记录修复，不触发卸载或强删pending。

## 本轮最终验证

本地Debian WSL **260项unittest通过，6.859秒，无失败/错误/跳过**（含6项新发行回归），compileall、self-check、Bash语法和ShellCheck通过；9个真实loopback TLS/HTTP场景通过。重新下载并验证官方Xray26.3.27、sing-box1.14.3、Caddy2.11.7，10项原生配置通过；官方Mihomo1.19.32三协议解析通过；Caddy真实IPv4/IPv6/双栈高端口监听通过。包逐文件/安装器GitHEAD比较、重复打包通过，Windows和Linux构建的payload一致。安装器SHA-256：`ac44e3d9dba4beed72b51efdc7034b52be2c0277823fa0d7b222a28808c223f0`。公共Git blob隐私审计及提交后CI另按最终提交核验，详情见交付记录。

WSL经Windows挂载的Git所有权校验需仅对本次进程显式设定可信目录（`GIT_CONFIG_COUNT=1 GIT_CONFIG_KEY_0=safe.directory GIT_CONFIG_VALUE_0="$PWD"`），不修改全局Git配置。测试没有在生产服务路径部署，没有将高端口监听写成公网IPv6/HTTP-01通过。隐私检查只读公共允许清单与Git blobs，不读取实际秘密作精确匹配；继承先前实机Token扫描的范围，不能声称本轮又扫过实际Token。

## 门禁与下一步

| 门禁 | 状态 | 适用边界 |
|---|---|---|
| 核心协议 | PASS | 同payload的既有Debian13 IPv4公网实测+本轮配置回归 |
| 升级恢复 | PASS | 真实v0.3→本地RC4、10reboot与故障；正式附件路径另列 |
| ACME | PASS | IPv4五节点/正式8张/9故障；自然临期长测NOT TESTED |
| 安全与隐私 | PASS | 审查范围未有未修复P0/P1；公共blob/包范围审计；不保证未知缺陷 |
| 发行包完整性 | PASS | 26文件、版本/tag、安装器HEAD、绑定摘要和重复构建 |
| GUI兼容性 | NOT TESTED | 三App逐协议待手动验收，不作已验收承诺 |
| 文档与支持范围 | PASS | 收窄已验收声明，最新报告入口，保留历史边界 |
| 正式在线安装 | NOT TESTED | v0.4.0附件尚不存在；发布后必须另做下载/安装/在线升级验收 |

明确回答：

1. **没有确定的未修复P0/P1**；发行门禁P1已最小修复并回归。
2. **不需要继续修改管理器核心代码**，其payload保持实机受测摘要；以后若改动则重新评估相关实机证据。
3. **具备建议解除Draft的代码条件**，前提是最终提交CI成功且评审认可本次收窄范围；本轮仍保留Draft。
4. **建议最终评审通过后合并main**；只是建议，本轮不合并、不切渠道。
5. **具备准备v0.4.0 Release的候选条件**；创建须用户后续授权、确认最终merge/tag指向和重新审计的三附件。正式在线交付不是已通过项。
6. 已知限制是GUI、其他系统/arm64/公网IPv6/复杂网站拓扑、256MiB整机及自然临期长期运行未验收；备份证书恢复为import、续期需重新配置；DNS失败可能留TXT需人工核对；第三方Reality选站不保证长期可用或抗封锁。
7. 不可省略的工作：最终提交CI成功；人工评审/用户发布授权；合并后对精确tag目标重新核对候选/HEAD与隐私；同一受审查版本的install.sh、tar.gz、sha256三附件；发布后在另行授权的干净测试系统执行真实在线新装/菜单/重复安装和v0.3在线升级，核对GitHub附件摘要、节点实连及原数据保护，完成前不标“在线交付通过”。GUI/其他平台若扩大承诺则先补对应实测。

本轮到候选审计与报告为止。历史更大范围的验收计划继续保留，不能因这些可选平台未测而虚构缺陷，也不能因GO判断自动操作PR或正式发行。
