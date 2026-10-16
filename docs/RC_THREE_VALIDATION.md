# v0.4.0 RC3：升级事务与续期 timer 验证

日期：2026-10-15。仓库 `km-hl/Node-Pilot`，分支 `codex/v0.4.0-stability`，现有 PR #1；审查基线与开始时 GitHub 最新提交均为 `507835ee695d86a06b5fbcc332c3ee3043bf9924`。PR 保持 OPEN / DRAFT，不合并 main，不创建 Release。程序版本仍为候选 `0.4.0`。

## 问题与修改位置

| 位置 | 触发、影响与最小修复 |
|---|---|
| `nodepilot_pkg/deploy.py:52,203,275` | 原成功恢复无条件 `enable --now`，会改变禁用、停止和临时启用策略；新事务在复制前保存原始状态，成功恢复按快照恢复 timer，首次安装才选择 enabled + active。 |
| `nodepilot_pkg/deploy.py:74,166,171` | 原回滚依赖进程内变量，掉电后丢失单元/owner/launcher 历史；日志增加完整快照及 preparing/installing/rolling_back/complete，回滚失败保留 rolling_back，后续进入管理器继续回滚。 |
| `nodepilot_pkg/deploy.py:19` | 以前只在目标文件不存在时检查 FragmentPath；现在始终检查实际加载来源，拒绝外部单元、drop-in 和符号链接。 |
| `nodepilot_pkg/deploy.py:118,330` | 原新复制的管理器在完整清单复核前执行 self-check，已有同摘要目录也缺少与当前 payload 的比较；现在首次执行前校验清单与目标 SHA-256，拒绝清单外 Python 模块，复制时排除本地字节码。 |
| `nodepilot_pkg/installation.py:33,42` | 分别读取 UnitFileState / ActiveState，按需执行 enable / enable --runtime / disable / start / stop，复读确认，不重启已经 active 的 timer。 |
| `nodepilot_pkg/installation.py:194,238` | 真实 systemd 矩阵发现 runtime timer 卸载可能留下 `/run` 启用链接；卸载及最终清理显式 disable --runtime。 |
| `nodepilot_pkg/manager.py:32,46` | 获取 flock 前迁移或重写 owner，锁竞争失败也可能改变标记；已有 marker 在锁前完全不写入，锁后才迁移。升级使用 `locked(migrate_owner=False)`，由持久事务管理 owner。 |
| `tests/test_release_candidate.py:405` | 六状态、首次安装、失败回滚、中断、旧日志兼容、安全拒绝及锁竞争回归。保留原 runtime 回滚测试，改用实际状态断言。 |
| `tools/program_smoke.py` | 新增可销毁 Linux 专用真实 systemd 矩阵；检查实际属性、原文件字节、配置 sentinel 和 `/etc`、`/run` symlink，拒绝任何既有 NodePilot 安装。 |

## 事务与安全策略

新日志继续使用外层 `schema=2`，以 `transaction_version=1` 区分新增格式，不修改节点 schema。持久化内容包括旧/目标版本、原修订、目标包摘要（destination）、逐文件 SHA-256、原 launcher、service/timer 完整 UTF-8 字节、各文件是否存在、timer 的两项状态、程序及状态 owner、原 source.json、私有备份位置和当前阶段/步骤。

在程序复制前记录 preparing。此阶段中断恢复旧状态，不执行不完整候选；部分 staging 目录保留以便检查，不递归删除未知文件。候选完整且校验通过后进入 installing，再切 current、owner、launcher、单元，daemon-reload，恢复 timer，最终 self-check 后标记 complete 并清除日志。普通失败进入 rolling_back，全部恢复成功才移除事务；恢复本身失败继续保留日志及备份，不报告完全恢复。

候选、清单、SHA-256、事务版本/路径/快照损坏时拒绝执行并保留证据。masked、未知 UnitFileState、转换中 ActiveState、外部 FragmentPath/drop-in/单元链接均明确拒绝；不会 unmask 或覆盖外部配置。正常升级保持现有启用链接与 active timer，不借单元更新改变用户策略；需要恢复已部分改变的状态时，分别清理两个链接作用域，再按原策略创建并验证。

旧 RC2 合法 intent 会先保留 `program-pending-rc2.json`，再补写兼容快照，保留恢复时可观察到的 timer 策略。**RC2 从未记录的原始状态无法事后推断**，例如 RC2 已把 disabled 改为 enabled 后，RC3 只能保留当前可观察状态；不能宣称找回当时的用户策略。旧程序存在但 timer 已缺失时，采用 disabled + inactive；只有没有旧修订且没有旧 timer 才默认启用。原始证据冲突时拒绝覆盖。未知未来事务不会按旧格式处理。

公开的 renew、菜单启动恢复与升级均在同一 manager 状态锁内运行；续期逻辑及三协议配置未改动。新 owner 仍为 schema=2，旧 v0.3 管理器不能操作该状态。发生准备/回滚中断且 current 仍指向旧版时，使用可信 RC3 源码安装器 `bash install.sh --local --upgrade` 恢复；不能指望不理解新日志的旧管理器自动处理。首次安装失败允许保留私有 owned 程序骨架及备份，timer 单元和两种启用链接会清理。

systemd 语义依据：[官方 systemctl 文档源](https://github.com/systemd/systemd/blob/main/man/systemctl.xml)。enable 调整启用链接，不等于 start；--runtime 使用运行时配置作用域；start/stop 独立改变运行状态；daemon-reload 重新加载定义，不赋予启用许可。本实现不用 `enable --now` 恢复升级状态。

## 自动化与真实 systemd 状态矩阵

环境：本机可销毁 Debian 13 amd64 WSL，PID 1 为真实 systemd；不是公网 VPS，也不是 Ubuntu/arm64 或完整 256MiB 主机。

| 升级前 | 成功升级后 | self-check 失败回滚后 | daemon-reload 后中断恢复后 |
|---|---|---|---|
| enabled + active | enabled + active | enabled + active | enabled + active |
| enabled + inactive | enabled + inactive | enabled + inactive | enabled + inactive |
| enabled-runtime + active | enabled-runtime + active | enabled-runtime + active | enabled-runtime + active |
| enabled-runtime + inactive | enabled-runtime + inactive | enabled-runtime + inactive | enabled-runtime + inactive |
| disabled + inactive | disabled + inactive | disabled + inactive | disabled + inactive |
| disabled + active | disabled + active | disabled + active | disabled + active |
| 首次安装，无 timer | enabled + active | 单元、永久/临时链接均清理 | 单元故障注入矩阵另验 |

六状态的前三列在 **v0.3.0 → RC3** 和 **RC2 基准 → RC3** 分别实测，共 36 组；不只断言 Mock 次数。读取真实 systemctl 属性并检查两个作用域的 symlink；失败后比对旧 current、owner、launcher、两个原单元逐字节相同，配置 sentinel 不变，重复 recover 返回无待处理事务。首次成功及失败清理另有两组真实 systemd 检查。每组结束卸载、检查全局路径和两种链接不存在。

模拟测试使用临时目录中的真实 symlink 及状态模拟器，**不是实际 systemd**。159 个 test 方法全部通过、无 skip（基线 148 项通过，新增 11 项）：

- 六状态加首次安装，成功及失败各 7 个子场景。
- 六状态加首次安装 × intent/candidate/current/owners/launcher/units/reloaded/timer/checked，63 个持久化边界组合；恢复执行两次。
- 文件复制中、候选目录落盘后中断；timer enable 与 start 已生效但步骤尚未完成时中断。
- 回滚 systemd 失败及回滚时再次中断，保留 rolling_back，再次恢复完成；最终 self-check 失败恢复原版本、owner、launcher 和旧单元。
- 旧 RC2 日志证据保留；masked/未知/转换状态/drop-in/外部 FragmentPath 拒绝；快照、摘要、额外模块、schema、状态路径、候选丢失均安全拒绝；锁竞争不写 owner；已有候选必须匹配可信 payload。

中断由受控 SystemExit 注入，绕过普通错误回滚，测试持久化恢复路径。真实 systemd 的 36 组仅在 reloaded 边界注入，其他细粒度边界用单元测试覆盖；**没有进行物理断电、SIGKILL 全矩阵或重启后 timer 行为验收**。

## 已执行检查

```bash
python3 -m unittest discover -s tests -v
bash -n install.sh
shellcheck install.sh
python3 nodepilot self-check
python3 tools/native_check.py --download
python3 tools/mihomo_check.py --download
python3 tools/program_smoke.py --disposable \
  --legacy-source .test-tools/legacy-source --legacy-source .test-tools/rc2-source
python3 tools/install_smoke.py
python3 tools/install_smoke.py --legacy-source .test-tools/legacy-source
python3 tools/upgrade_smoke.py --legacy-app .test-tools/legacy-source
python3 tools/service_smoke.py
python3 tools/http_family_smoke.py
```

- Bash 语法、项目本地 ShellCheck 0.10.0、自检通过。ShellCheck 仅工具目录加入 PATH，未改系统配置。
- 官方下载 SHA-256 / ELF / 版本检查通过：Xray 26.3.27、sing-box 1.14.3、Caddy 2.11.7；8 项原生配置检查通过。
- 官方 Mihomo v1.19.32 三协议导出解析通过，不代表 GUI 或公网连接。
- v0.3.0 三种真实临时节点的 metadata、URI、原生配置、MainPID 监听保持；端口修改再恢复、旧管理器拒绝 schema=2 通过，临时节点清理。
- 三协议 systemd 服务、Reality/HY2 合法复用端口号的 TCP/UDP 监听、真实重启失败回滚、删除隔离通过；Caddy 本机 IPv4、IPv6、双栈高端口 HTTP/PID 归属通过。
- 首次安装自动 TTY、重复安装保留配置、源码升级、cwd 同名包隔离、无效菜单循环、stdin 管道读取 TTY、卸载，以及可信 v0.3.0 安装器升级检查通过。
- 可复现候选包 Windows/Linux 字节一致；installer 摘要重新绑定，未上传任何 dist、内核、节点配置、Token 或私钥。

运行日志在本地忽略目录 `.test-output/rc3-*.log`，不作为源码上传。复现 systemd 测试前必须使用可销毁的授权环境；脚本自身拒绝任何既有 NodePilot 路径，不在生产机直接运行。

## 候选产物与剩余验收

候选包 `nodepilot-0.4.0.tar.gz` SHA-256：`77d074b9c53e09fcce92611af0ee36c1518d3e4d96a7d020a298237bfd034ace`。安装器继续指向尚未发布的未来 v0.4.0 附件，本轮不创建正式 Release；使用可信完整源码的 --local 入口验证。

**可进入最终发布验收阶段，不建议现在合并或宣称 v0.4.0 已满足正式发布条件。** 本轮范围内升级 timer 状态和持久回滚问题已修复；已知边界如下：

1. 真实 Cloudflare DNS-01 TXT 创建/传播/失败清理、Staging/正式 ACME 签发、自动续期、实际插件日志脱敏及 Caddy 新证书加载仍未验收。模拟签发或配置检查不能替代真实授权域名/Token 测试。
2. 部分公网 IPv6、IPv6-only/双栈 HTTP-01、云安全组、Ubuntu/Debian 各版本和 arm64 实机升级仍未验证。
3. 本轮无整机重启/断电证明，WSL systemd 与异常注入不代表 VPS 开机行为；enabled-runtime 重启后失效是预期语义，但本轮未实际重启验证。
4. 没有重测完整 1C/256MiB 整机或真实 ACME 峰值。管理器仍仅使用 Python 标准库、按需启动、无新增常驻进程；不能据此保证低内存多协议长期稳定。此前三协议 cgroup 触及 256MiB 的限制继续保留，见 RC2 报告。
5. 旧 RC2 日志缺失的历史不可恢复；损坏事务或外部单元变更可能需要人工核对。部分 staging 和原始日志作为证据保留，不承诺自动清理所有磁盘残留。

未修改全局防火墙、IPv6、路由、swap 或 sysctl；没有访问或升级生产 VPS。
