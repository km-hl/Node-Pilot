# v0.4.0 候选验证

日期：2026-10-14。基准 v0.3.0 / 548e842。审查及实施顺序见 [计划](VERSION_FOUR_PLAN.md)。

## 运行结果

执行命令：

```bash
python3 -m unittest discover -s tests -q
bash -n install.sh
shellcheck install.sh
python3 nodepilot self-check
python3 tools/native_check.py --download
python3 tools/mihomo_check.py --download
```

真实临时 VPS 另运行 `tools/stability_smoke.py`、`tools/resource_matrix.py` 和外部 `tools/client_probe.py`；这些工具有明确系统副作用，只可在授权测试环境执行。干净安装测试使用 root `tools/install_smoke.py`。

- Linux 单元、配置、安全和事务测试：121 项通过。覆盖地址族、证书复用/失败、Token 私有存储/日志脱敏、DNS 参数、服务退出/重启/监听、卸载恢复、候选池/多地址/组检测和旧 metadata。
- 干净 Debian systemd WSL（不是外部 VPS）：新安装自动进 TTY、重复安装保留 sentinel、源码升级、无效输入循环、stdin 管道下 /dev/tty 交互及完整卸载通过，测试路径/单元清理完成。
- bash -n、shellcheck、管理器 self-check 通过。
- 重新从官方发布下载 Xray 26.3.27、sing-box 1.14.3、Caddy 2.11.7，校验官方 SHA-256、ELF 和报告版本。
- 8 项原生检查：三协议、Caddy HTTP-01/导入/DNS-01 PEM/测试自签名、self Reality 配置。DNS-01 原生检查验证 Caddy 加载 PEM，不能作为真实 DNS/CA 签发证据。
- Mihomo v1.19.32 官方 SHA-256 校验下载，三协议导出 YAML、IPv6、引号/中文/密码转义通过原生解析。未执行该客户端公网或 GUI 矩阵。
- Debian 13 amd64 真实 VPS：三服务 MainPID 监听检查、本机 Reality 经代理 HTTPS 读取数据通过；错误 Reality 公钥失败，日志不足时明确归为 connection_or_handshake，不伪造精确认证错误。
- 三协议外部 sing-box 客户端分别 4MiB、2 并发，SHA-256 一致，另经代理访问 HTTPS，正常验证证书；自签名 TLS 显式导入信任，未跳过验证。
- 真实服务故障注入：续期网络失败后旧证书/修订/监听保持；卸载第二服务停止失败后全部服务恢复；最终清理中断后的续做另行检查。CA 失败使用隔离故障注入，不冒充真实 CA 续期。
- 原 x-ui 保持运行，原配置 SHA-256 与测试前一致；不操作原网站/网络参数。
- 从已校验的 v0.3.0 安装程序创建三个隔离旧节点/单元，由 v0.4.0 读取并通过原生配置/真实监听检查，分享 URI 一致；修改端口再恢复后，三个旧配置均经真实代理数据和 HTTPS 检查，临时旧节点清理完成。

## 修改文件对应

| 文件 | 改动 |
|---|---|
| certificates.py / system.py / caddy.py | 地址族、证书复用、续期状态、DNS-01 PEM 安装 |
| dns_credentials.py / acme_runner.py | 私有 Token、Zone 检查、日志/异常栈脱敏及 SDK 配置隔离 |
| installation.py / manager.py | 卸载预检、备份/进度、恢复和最终清理；旧节点兼容、目标未变时保留 |
| targets.py | 候选池、实际组检测、64KiB 输出限额、分地址测量、HTTP 质量提示 |
| services.py / connectivity.py | systemd/主进程监听诊断、本机 Reality 代理 HTTPS 与当前修订测试记录 |
| menu.py / exporters.py / cli.py / model.py | 证书菜单/详情、变更确认、Mihomo YAML、新字段校验及恢复入口 |
| project_update.py / __init__.py / install.sh / package.py / release_audit.py | v0.4.0 候选版本、旧脚本降级保护、固定包 SHA-256 |
| test_features.py / test_reliability.py / test_targets.py 等 | 新安全/失败/兼容性测试与现有回归 |
| stability_smoke.py / resource_matrix.py / resource_probe.py / native_check.py / mihomo_check.py | 实机生命周期、资源矩阵、官方客户端/内核验证 |
| README / CHANGELOG / REALITY_FLOW / VERSION_FOUR_PLAN / privacy_audit.py | 使用、恢复边界、限制、审查记录和公开上传白名单 |

## Reality 选站

VPS 上新池 8 个候选均取得实际 X25519、TLS1.3、h2 和可信证书匹配结果。Amazon/AWS 根页面正常，Samsung/Nvidia/AMD 有跳转，Intel/Sony/Oracle 返回 403；后两类只记录质量提示。IPv6 没有可用路由，逐地址记录失败，没有误判 IPv4 同样不可用。

某次排名第一为 Nvidia，握手中位约 45ms、波动约 2ms；排名随 VPS/DNS/时段变化，不是固定默认 SNI，也不是长期稳定性证明。测量包含 OpenSSL 子进程开销，最多每族三个地址，剩余地址标为未测；CF 官方 IP/DNS/可取得的响应头检测保留。选中的自动目标固定到当前验证 IP，域名/SNI 与证书仍匹配，后续 DNS/CDN 变化需重新测量。

## 受限内存矩阵

实际主机为 1 CPU/约 2GiB，测试组限制 1 CPU、swap=0，每组稳态采样 24 秒。冷复制/校验内核、单协议或三协议启动、原生检查、重启、OpenSSL 测试证书生成、每协议 2MiB 加密请求和 HTTPS。Reality 组含共享 Caddy；三协议组为 Xray、两个 sing-box、Caddy及测试 Python。流量客户端在同 VPS 的组外，不能当成整机隔离或外网时延基准。

| 上限 MiB | Reality 组 kernel peak / core RSS MiB | AnyTLS | HY2 | 三协议 |
|---|---|---|---|---|
| 256 | 122.21 / 78.73 | 115.38 / 60.91 | 113.87 / 62.78 | 256.04 / 183.23 |
| 512 | 118.32 / 79.96 | 113.64 / 60.83 | 113.84 / 62.61 | 233.79 / 209.05 |
| 1024 | 118.86 / 79.09 | 113.91 / 61.08 | 111.37 / 61.86 | 236.08 / 207.49 |

12 组完成，memory.events oom/oom_kill 均为 0，swap 为 0。256MiB 三协议组额外执行 sing-box 1.14.3 候选下载、SHA/ELF/版本验证、所有受影响配置检查和替换启动，max=698；kernel peak 可能因内核瞬时计费略高于设定限制。其峰值受限制/回收影响，不能解释为自然内存需求。core RSS 不包含 Python、内核及文件页缓存，kernel peak 包括组内这些计费项。

没有测试完整 256MiB/512MiB/1GiB 实机的 OS、SSH、真实 ACME 签发/传播和长期负载，不能宣称这些整机已验收。未自动修改 swap、全局 sysctl、IPv6、路由或防火墙。

## 功能使用和恢复边界

证书选项 1 HTTP-01、2 Cloudflare DNS-01、3 PEM、4 自签名。DNS Token 仅限所需 Zone 的 DNS:Edit/Zone:Read；私有 profile 0600，分享/YAML/单元不含 Token。DNS 插件由签名系统源安装，独立 runner 在日志落盘前脱敏，并隔离 SDK 全局凭据和 certbot cli.ini。Zone ID 必须匹配域名归属；默认传播等待 60 秒，可设 10–600 秒。独立 NodePilot timer 安装更新后加载 Caddy PEM。

菜单 10 查看证书域名/issuer/到期/天数/自动任务运行/近期状态、强制 certbot 续期及 YAML 导出。Caddy HTTP-01 自身自动续期，暂无强制手工签发按钮。节点修改前有摘要和确认，秘密只显示“已变化”。菜单 8 可选本机 Reality HTTPS 测试，公网仍未验证；已有结果对不同节点修订不复用。

卸载先预检/备份/记录，再停服务，配置删除前的失败会恢复原服务。`finalizing` 后仅继续项目文件清理，不能回滚成原安装；程序已部分删除时需用可信 v0.4.0 或更新源码执行 `bash install.sh --local --upgrade` 修复管理器，再进菜单续做。候选期 main 仍是 v0.3.0，不能用于恢复新卸载事务。私有备份/凭据/接管目录/事务记录保留，不以简单“原子卸载”描述。活跃接管必须先归还。

## 发布判断和待验证项

源码、基础原生配置、临时 VPS 生命周期与 cgroup 短测可交付为候选；暂不发布 v0.4.0 稳定版。真实 Cloudflare TXT 创建/传播/清理、CA 签发及自动续期仍缺可用域名/Token；日志脱敏机制已单测但真实插件会话仍需验证。IPv6-only/双栈签发和 Ubuntu/arm64 实机、Mihomo/v2rayN/Egern GUI、多小时低内存长测尚未完成。

候选包 SHA-256 与 installer 绑定一致，正式上传前另对 Git HEAD 和包的显式文件清单审核；main 稳定安装命令不更换为尚无附件的候选入口。没有把配置检查、Mock、cgroup 或本机测试写成公网/CA/整机成功。
