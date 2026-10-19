# v0.4.0 评审、合并与发行检查清单

**后续状态（2026-10-19）：** PR #1 已合并；发行tag `v0.4.0` 固定指向 `6900d17c0127a18c3c876e1b28757e3a5eb17419`。原三附件重建/公开下载/Release Audit一致，[真实在线交付](VPS_TEST_REPORT.md#online-delivery-2026-10-19) 为 **ONLINE DELIVERY PASS**；无新增已确认未修复P0/P1。用户单独授权后，同一 Release ID `416519274` 原地转为 **Stable / Latest**，tag/附件未改，后续main仅更新文档，本轮未访问VPS。完整实测范围仅Debian13 / amd64 / IPv4 / 约2GiB；干净OS依赖安装、正式CA节点在线升级、其他平台/公网IPv6/GUI、256MiB整机与自然临期长期运行仍不作已验收承诺。下文为原审查/分阶段清单，保留各轮当时状态；当前说明见 [Release Notes](RELEASE_NOTES.md)。

本清单把后续命令和验收条件做成可审查的交接内容，不代表已经获得发布授权或后续阶段已完成。当前执行记录以 PR / CI 和交付报告为准。基准 `334572ac68221987b499c63b1d43298fe6272baa`；本次后续提交只整理文档，管理器和安装器无变化。

## 1. 当前授权：最终 PR 评审

- [x] fetch 并核对 PR #1 原 HEAD 为334572ac、main 为548e84202589dc0be5898a02552541a0065a6de5，无冲突，原精确提交两项CI成功。
- [x] 复核 Release Audit、许可证、README/CHANGELOG、支持范围和公共 Git blobs；没有确定的未修复P0/P1。
- [x] 管理器与c63106f受测字节一致；重建并审计候选三附件，不把历史包直接当新证据。
- [ ] 文档提交后核对新的精确HEAD与全部CI，满足条件时将PR标为Ready for review；此项结果以实际PR为准。
- [ ] **停止在合并门禁，等待用户明确批准。** Ready不等于独立维护者已经Approve，也不等于已经合并。

## 2. 单独批准合并后

1. 再fetch、记录PR head、main head、无冲突、全部CI成功及未解决意见；任一受审查内容变化先重评。
2. 正常PR合并，绑定用户批准的精确head；不得force、admin绕过、跳过CI或删除保护。可按仓库可用合并方式执行 `gh pr merge 1 --merge --match-head-commit <批准的HEAD>`；若策略要求另一方式，先核对策略，不改保护。
3. 记录实际merge commit及最终main SHA，不假设等于PR HEAD；比较最终main与受审查分支的完整tree，排除合并带入未审查变更。
4. 在干净的最终main检出上重新运行第三节，等待main精确提交CI。此时不能复用未经main核验的候选附件。

**渠道窗口：** 合并后的main安装器将引用v0.4.0固定附件，在Prerelease附件可访问前，新在线安装会安全失败。应预先准备好已审查的后续步骤，将合并/发行安排在用户确认的发布窗口；附件健康前不推广main安装命令，不把404当已验收，不以篡改v0.3附件规避窗口。

## 3. 最终main候选准备（须确认进入该阶段）

确认工作区干净、HEAD为第二节实际main SHA，源码版本0.4.0；再执行：

```bash
python3 -m unittest discover -s tests -v
python3 -m compileall -q nodepilot nodepilot_pkg tools tests
python3 nodepilot self-check
bash -n install.sh
shellcheck install.sh
python3 tools/package.py --repo km-hl/Node-Pilot --tag v0.4.0
python3 tools/release_audit.py --repo km-hl/Node-Pilot --tag v0.4.0
python3 tools/package.py --archive /tmp/nodepilot-repeat.tar.gz
cmp dist/nodepilot-0.4.0.tar.gz /tmp/nodepilot-repeat.tar.gz
python3 tools/privacy_audit.py
sha256sum dist/install.sh dist/nodepilot-0.4.0.tar.gz dist/nodepilot-0.4.0.tar.gz.sha256
```

所有命令必须成功，不能用最后一条的返回码掩盖前序失败。既有CI另含官方内核配置、Mihomo解析、loopback TLS与地址族监听检查；有核心变更时重新评估相应实机证据。记录tar逐文件匹配HEAD、安装器逐字节匹配HEAD、唯一URL/SHA绑定、版本/tag/附件名一致、无私有文件和可复现摘要。

本轮重新计算的参考payload SHA-256为 `645d116e5dd1ceceb4c7af3e5b372997e9de36010f9f29481e4bb46e5c2990ae`，安装器为 `ac44e3d9dba4beed72b51efdc7034b52be2c0277823fa0d7b222a28808c223f0`；后续必须重新计算，不能把参考值代替最终main/tag审计。

## 4. 单独批准tag和Draft Release后

- 先查询远端v0.4.0 tag及Release是否已存在；若存在，核对其实际指向与已发布资产，不能force移动公开tag或静默替换附件。
- tag必须指向第三节已审查的最终main提交；核对远端tag的实际commit。
- 创建 `Node-Pilot v0.4.0` Draft Release，标记Prerelease且不设latest；上传同一检出重新生成的 `install.sh`、`nodepilot-0.4.0.tar.gz`、`nodepilot-0.4.0.tar.gz.sha256`，只上传这三件。
- 从Draft重新下载三附件到空私有目录，逐字节/摘要比较本地受审查附件；再以下载附件运行现有release_audit，确认实际tag tree及安装器一致，附件无缺失/重名。
- Release Notes使用 [双语草稿](RELEASE_NOTES.md)，保留线上新装/升级“尚未验收”的真实状态。
- **取得发布Prerelease的明确批准后**才让固定下载URL公开可访问；再次核对tag、附件和不设latest。此步骤不提升稳定版，不改历史v0.3附件。

## 5. 单独授权独立可销毁VPS后

不得自动沿用旧VPS地址/密码或历史授权。确认具体测试实例、SSH凭据的私有提供方式、允许安装/升级/卸载及故障注入的范围；不在本清单放真实地址或凭据。

使用真实v0.4.0 Release附件，复用现有安装器、tools/vps_smoke.py和client_probe.py；不增加第二套测试框架，不把--local当线上验收。

| 场景 | 必须通过的检查 |
|---|---|
| 在线新装 | GitHub固定URL真实存在，实际安装器/包摘要匹配审计；下载→校验→部署→自动TTY菜单，版本0.4.0；官方内核下载校验，三协议创建、URI/Mihomo导出及外部数据摘要/HTTPS；重复安装不变；自有卸载边界 |
| v0.3在线升级 | 独立测试安装正式v0.3，先建真实可用三节点，保存配置/认证/证书/URI/单元/PID/timer双状态/关键摘要；真实v0.4安装器--upgrade下载真实包；版本、来源与三协议重连/HTTPS通过，旧配置凭据和timer策略保持；重复升级安全 |
| 错误/恢复 | 真实附件404、HTTPS失败、摘要不符、坏归档、重复安装、旧状态未完成事务、外部同名路径；仅在授权环境受控注入，核对拒绝/可恢复且原节点/外部服务不受破坏 |
| 收尾 | 测试节点/单元/DNS仅按所有权清理，保护原数据和日志；记录实际命令、返回码及脱敏结论，秘密与内部产物不公开 |

故障注入明确区分真实下载故障与夹具；客户端保持正常TLS验证。需要新ACME/DNS测试时另外确认域名/Zone权限，不从本轮授权推断可使用历史Token。并非所有协议创建都需要新正式签发：测试自签根可显式导入，必须标注证据类型。

## 6. 单独批准提升稳定版后

仅当最终CI/审计/三附件/真实在线新装/在线升级/三协议公网连接/隐私均通过、无未修复P0/P1且未测范围标记准确时，才能请求用户批准把Prerelease提升为稳定版；确认是否设latest。此前必须保持Prerelease。

如果验收失败：暂停推广、保持Prerelease、保存证据；不悄悄覆写公开附件、force移动tag、删除用户节点或强制降级。修复要重新评审源码身份、发行版本/资产策略与相关验收。

最终报告记录PR/merge/main/tag各SHA、Release状态/URL、三附件及各自SHA-256、CI链接、审计与真实线上验收结果、遗留限制；只能写实际完成的阶段。同步README/CHANGELOG实际发布状态，不提前声称稳定版已发布。
