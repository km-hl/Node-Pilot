# Node-Pilot v0.4.0 — Stable Release Notes

**Stable / Latest（2026-10-19，Asia/Taipei）。** 在原 Release ID `416519274` 上转正；未重建 Release、移动 tag 或替换附件。发行源码固定为 `6900d17c0127a18c3c876e1b28757e3a5eb17419`，main 的后续提交仅更新文档。公开附件在线新装/升级验收为 **ONLINE DELIVERY PASS**，仅适用于下述实测范围。

**Stable / Latest as of 2026-10-19 (Asia/Taipei).** The existing Release was promoted in place; its tag and assets are unchanged. Source commit: `6900d17c0127a18c3c876e1b28757e3a5eb17419`. Subsequent main commits update documentation only. **ONLINE DELIVERY PASS** applies only to the tested scope below.

## 中文

### 功能与改进

Node-Pilot是使用Python标准库和终端菜单的VPS节点管理器，无Web管理后台。提供VLESS + TCP + REALITY + Vision（Xray）、AnyTLS和Hysteria2/QUIC（sing-box）；这些基础协议沿用已有版本，v0.4.0重点完善证书、诊断与恢复。

- Reality目标筛查与固定IP/显式动态DNS、独立SNI及Cloudflare检测诊断；可使用隔离Caddy作为自建目标。
- ACME HTTP-01与Cloudflare DNS-01、证书复用/续期状态、导入PEM及明确标记的测试自签名；不默认关闭TLS验证。
- 多节点创建、修改、删除、分享与私有Mihomo配置导出；官方内核版本选择、升级及失败回滚。
- 持久程序升级/卸载恢复、timer启用与活动状态分别保留、complete事务只读最终化、更新来源显式确认。
- 独立Xray/sing-box服务的显式接管与归还、备份及空节点恢复；外部同名单元/FragmentPath或drop-in不匹配时拒绝操作。
- 发行门禁核对Git HEAD、安装器字节、版本/tag、包白名单和SHA-256；可复现构建。

### 已有稳定性证据

同一管理器payload在Debian13/amd64/IPv4实机完成三协议公网数据与HTTPS/TLS校验、错误认证、端口冲突/失败回滚、备份恢复及Caddy/临时独立服务接管归还。正式v0.3.0新装到可信本地RC4升级及10组真实整机reboot/事务恢复通过。

真实ACME先Staging后有限正式签发：三DNS-01与两个Caddy HTTP-01目标，正式8张证书（5首次+3续期），9组故障保留旧修订。Caddy自动续期使用Staging加速窗口，其他续期为手动强制，不能作为自然临期长期运行证明。2026-10-19 真实公开附件在线新装、自动TTY菜单、重复安装、正式v0.3.0→v0.4.0在线升级与重复升级通过；保留原节点认证/配置/证书/单元及timer策略。独立sing-box客户端六阶段三协议共27MiB数据摘要核对、18次正常HTTPS通过，下载/摘要/归档拒绝、受控回滚和持久intent恢复通过；受控夹具不冒充自然故障。

### 支持范围和限制

**完整实测范围仅为 Debian 13 / amd64 / 公网 IPv4 / 约2GiB RAM（1 CPU）。** 包含报告所列协议、ACME、升级/重启和公开附件在线交付。在线轮是在既有主机腾空NodePilot路径后新装，依赖已具备；AnyTLS/HY2用显式信任的测试证书且正常TLS验证，未重测正式CA节点在线升级。干净OS依赖安装/峰值不在此结论内。Debian12、Ubuntu整机、ARM64、IPv6-only/双栈公网ACME、GUI客户端、复杂Docker网站拓扑及自然临期长期续期未完成完整验收。

180秒1CPU/256MiB进程组峰值166.2MiB、无OOM，主机实际约2GiB；这不是256MiB整机安装、ACME峰值或长期稳定保证。Mihomo原生配置解析不代表Clash Verge Rev/v2rayN/Egern GUI已实连。不实现XHTTP/CDN、其他代理协议、Web面板或订阅服务器。

### 安装、升级与安全

使用下方固定v0.4.0 Release安装入口；在线升级添加 `--upgrade`。三个公开附件、SHA-256和安装器绑定已验证；v0.4.0已设为Stable / Latest，历史v0.3.0 Release和附件不变。

安装前备份重要数据；妥善保管Cloudflare限定Zone的API Token，勿公开节点认证、完整URI、Reality/TLS私钥或私有日志。备份中的证书恢复为导入模式，不自动继承ACME续期授权，须重新配置。DNS API故障/强制中断可能留下TXT，需核对本项目挑战记录；不建议在未经评估的生产VPS上做破坏性测试。

## English

### Features and improvements

Node-Pilot is a terminal VPS node manager built with the Python standard library. It manages VLESS + TCP + REALITY + Vision through Xray, and AnyTLS / Hysteria2 through sing-box. These base protocols continue from earlier versions; v0.4.0 focuses on certificates, diagnostics and recovery.

- Reality target screening, pinned IPs or explicitly confirmed dynamic DNS, independent SNI, Cloudflare diagnostics and isolated Caddy self-hosted targets.
- ACME HTTP-01 / Cloudflare DNS-01, certificate reuse and renewal status, PEM import and clearly labeled self-signed testing. TLS verification stays enabled by default.
- Multi-node lifecycle, sharing and private Mihomo export; official core version selection, upgrades and rollback.
- Durable program/uninstall recovery, separate timer enablement and activity, read-only completion of committed transactions, and explicit update-source changes.
- Explicit standalone-service adoption/return, backups and empty-state restores, with ownership and loaded-unit checks.
- Reproducible release payloads audited against Git HEAD, installer bytes, version/tag, file allowlist and SHA-256.

### Validation and support

**Fully tested scope is limited to Debian 13 / amd64 / public IPv4 / approximately 2GiB RAM (1 CPU).** Existing evidence covers public traffic and verified HTTPS for all three protocols, negative authentication, conflict/rollback/restore, Caddy and temporary standalone-service adoption. A genuine v0.3.0 Release installation upgraded to trusted local RC4, followed by ten whole-host reboot/recovery cases.

Real ACME validation used Staging before limited production issuance: three DNS-01 and two Caddy HTTP-01 targets, eight production certificates and nine failure cases preserving previous revisions. Accelerated Staging Caddy renewal and manual forced renewals do not establish long-term natural-expiry renewal. On 2026-10-19, actual public-asset installation, automatic TTY menus, repeated installation, v0.3.0-to-v0.4.0 online upgrade and repeated upgrade passed, preserving node credentials, configuration, certificates, units and timer policy. Independent sing-box clients transferred 27MiB with integrity checks and completed 18 verified HTTPS checks across six stages. Download/digest/archive rejection, controlled rollback and durable-intent recovery passed; controlled fixtures are distinguished from natural failures. This was a fresh NodePilot installation on an existing host with dependencies already available, using explicitly trusted test certificates with normal TLS verification. Clean-OS dependency installation and production-CA node online upgrades were not tested in this round.

Debian12, full Ubuntu VPS lifecycle, ARM64, public IPv6/dual-stack ACME, GUI clients, complex Docker website topology and natural-expiry long-term operation remain unverified. The 180-second 1CPU/256MiB process-cgroup test peaked at166.2MiB without OOM on a roughly2GiB host; it does not prove a256MiB whole-host installation or long-term operation. Mihomo parsing is not GUI connection validation.

### Installation and safety

Use the fixed v0.4.0 Release installer below; add `--upgrade` for upgrades. v0.4.0 is now Stable / Latest; the historical v0.3.0 Release and assets remain unchanged. Back up important data, restrict Cloudflare Tokens to the required Zone, and keep credentials, complete URIs, private keys and private logs confidential. Restored certificates use import mode and do not inherit automatic-renewal authorization. Check project-owned challenge TXT records after provider failures or forced interruption. Destructive testing requires an explicitly authorized disposable environment.

完整证据与支持分级 / Evidence and boundaries: [Release Audit](RELEASE_AUDIT.md)、[VPS报告](VPS_TEST_REPORT.md)、[GUI清单](CLIENT_ACCEPTANCE.md)、[执行检查清单](RELEASE_CHECKLIST.md)。

## 固定下载与摘要 / Fixed downloads and SHA-256

```bash
bash <(curl -fsSL https://github.com/km-hl/Node-Pilot/releases/download/v0.4.0/install.sh)
# Upgrade / 升级
bash <(curl -fsSL https://github.com/km-hl/Node-Pilot/releases/download/v0.4.0/install.sh) --upgrade
```

| 附件 / Asset | SHA-256 |
|---|---|
| [install.sh](https://github.com/km-hl/Node-Pilot/releases/download/v0.4.0/install.sh) | `ac44e3d9dba4beed72b51efdc7034b52be2c0277823fa0d7b222a28808c223f0` |
| [nodepilot-0.4.0.tar.gz](https://github.com/km-hl/Node-Pilot/releases/download/v0.4.0/nodepilot-0.4.0.tar.gz) | `645d116e5dd1ceceb4c7af3e5b372997e9de36010f9f29481e4bb46e5c2990ae` |
| [nodepilot-0.4.0.tar.gz.sha256](https://github.com/km-hl/Node-Pilot/releases/download/v0.4.0/nodepilot-0.4.0.tar.gz.sha256) | `e6f6bd414c68c6db3b57fe235b78c9c58c515f1b46ca486ee006cb3f5a5a6461` |

最后一行是校验文本自身摘要；正文绑定上行payload。The last hash covers the checksum text file itself, whose contents bind the payload hash above.

[在线交付 / Online delivery](VPS_TEST_REPORT.md#online-delivery-2026-10-19) 为后续新增证据；tag内文档保留转正前历史。Documentation in the immutable tag retains the historical pre-promotion state; subsequent main documentation records online acceptance and Stable promotion.
