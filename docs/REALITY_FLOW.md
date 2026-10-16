# Reality 默认流程与第三方选站

v0.2.0 默认参考 [Reality 偷自己 / Caddy 管理证书教程](https://jollyroger.top/sites/384.html)，重新实现独立服务和安全事务，未复制其脚本。

## 有域名：默认自建目标

1. 用户提供自己控制的域名。HTTP-01 要求全部 A/AAAA 直指 VPS；DNS-01 通过域名控制权签发，不要求 DNS 指向，但分享地址必须能访问 VPS。SNI、Xray serverNames 和证书域名使用同一值。
2. Xray 在用户选择的公网 TCP 端口监听 VLESS/Reality/Vision；本机目标为 `127.0.0.1:<独立端口>`、xver=1。
3. 独立 `nodepilot-caddy.service` 聚合全部自建目标，每个节点一个 loopback TLS 监听。PROXY wrapper 仅信任 127.0.0.1；不开放默认 2019 管理接口，管理器通过 root 私有 Unix socket 重载配置。
4. 默认由 Caddy 使用 Let's Encrypt HTTP-01 自动申请及续期，邮箱可选。HTTP-01 按实际地址族共享公网 TCP 80，TLS-ALPN 挑战关闭。占用时可选独立 certbot Cloudflare DNS-01 或导入 PEM；不停止其他网站。官方 Caddy 无需包含 DNS 插件，DNS-01 PEM 由 NodePilot timer 安全更新；测试自签名需明确选择。
5. Caddy 默认返回简单欢迎页，也可反向代理用户指定的 HTTPS 域名；网页来源不是 SNI。关闭 Caddy HTTP/3，避免增加资源及占用 HY2 的 UDP 端口。
6. Xray 原生检查、Caddy 原生检查、聚合配置重载及本机证书/TLS 1.3/h2 就绪后才报告创建成功。任何一步失败恢复旧修订和聚合配置。多个自建节点共用一个 Caddy；删除一个仅去掉其监听，最后一个删除时停止本项目 Caddy。

[Caddy 自动 HTTPS](https://caddyserver.com/docs/automatic-https)、[PROXY wrapper](https://caddyserver.com/docs/caddyfile/options#proxy_protocol)、[官方 ACME 邮箱可选定义](https://github.com/caddyserver/caddy/blob/master/modules/caddytls/acmeissuer.go) 支持以上配置。导入的测试证书只用于验证流程，不能当作公网可信 ACME 证书。

自建目标备份包含证书及私钥，恢复为导入模式；ACME 账户没有被迁移，需重新选择自动证书。Caddy storage 在 `/etc/nodepilot/caddy/`，删除/卸载保留私有历史证书和备份；不操作系统 Caddy 文件。

## 没有域名：从本 VPS 筛选第三方目标

[REALITY 官方说明](https://github.com/XTLS/REALITY/blob/main/README.en.md) 仍建议支持 TLS 1.3、HTTP/2、域名不用于跳转且 IP 靠近代理服务器。TLS/组/证书决定握手可行性，HTTP 重定向发生在握手之后。v0.4.0 将 HTTP 跳转/非 2xx 分开标为网页质量警告；允许选择不表示它与无跳转站点有同等伪装质量，仍需人工评估和实际客户端检测。排名不能保证抗封锁或客户端端到端时延。

- 内置 Amazon/AWS、Samsung、Nvidia、AMD、Intel、Sony、Oracle 仅是候选；去除 Apple/Google 是推荐策略，不是协议无效判定。也可输入任意合规的 1–12 个自选候选。
- 从 [Cloudflare 官方公开 API](https://developers.cloudflare.com/api/resources/ips/methods/list/) 获取当前 IPv4/IPv6 网段，失败时尝试官方文本列表。全部来源失败则停止选站，避免将未知结果当作非 CF。
- DNS 查询有独立进程超时，拒绝非公网地址；任意 DNS 地址落在 CF IP 段、规范名称有 CF 标记即排除。使用系统 getaddrinfo 的 A/AAAA 和可见规范名，不宣称审计了完整 CNAME 链（[Python 官方语义](https://docs.python.org/3/library/socket.html#socket.getaddrinfo)）。仅使用 Cloudflare 权威 DNS 不等于使用其 CDN，不按 NS 一刀切排除。
- OpenSSL s_client 保留可信链和主机名验证，显式要求 TLS1.3/h2 并提供 X25519，再读取实际协商组；没有可靠组结果标为未验证。每地址三次测量，取中位数和极差，测量包含子进程开销。HTTP 请求固定在筛选 IP，不跟随跳转；任何已测地址有 CF 头标记即排除整个候选。200/301/302/403/404 等有效最终响应头可以检查 CF；HTTP 质量与 TLS 可行性分开。头部超时、拒绝连接、不可达、证书错误、无效/未完成响应各自记录原因，不标完成。自动排名只收录网段/可见 DNS/所选地址头检查均完成的候选，优先已完成检查的 IP。
- 最多测试每族三个地址，交错检测 IPv4/IPv6，一个失败继续其他地址；超过上限明确未测。自动与手动默认固定到已验证的 IP，避免内核重新解析时拨到失败地址；原输入另存 requested_target。DNS/CDN 地址会变化，只声称排除了当前检测到的 CF，不能保证未来地址。旧节点升级和目标未变的编辑不强制重选。
- 名单测量来自 VPS 到目标，不能用开发者电脑的 ping 代替。低握手时延不代表高吞吐，也不能证明 Reality 真正握手成功；必须从真实客户端再验证。

独立重测命令（在 VPS 中运行）：

```bash
nodepilot targets
nodepilot targets --domains www.amazon.com,aws.amazon.com,www.samsung.com
```

已有节点修改可输入 `keep` 保留原第三方目标；目标/SNI 未变时不重新筛选，变更摘要须确认。自建转第三方输入 `-`；反向切换填写自己的域名。证书失败不静默改用第三方 SNI。

输入 `manual` 可指定 target 域名/IP、端口及客户端 SNI，执行当前 CF、可信证书、TLS1.3/h2/X25519 与三次检测，网页质量单独提示。默认模式 1 使用检测返回的 IP:port；SNI 与 target 原域名可以不同，仍须正常验证该 SNI 证书。IP 输入保留原地址。模式 2 保留域名动态解析，须明确确认：运行时 DNS 可能变化、后续 IP 未必经过检测、本次通过不代表所有解析地址可用、DNS 状态不具有固定 IP 漂移含义。确认摘要后才修改待保存参数，最终配置保存仍沿用原确认/原生检查/事务回滚。

结果中的 `checks` 分开记录 `ip_ranges`、`dns_cname`、`http_headers` 和 `tls`；`feasible` 表示 TLS 适配，`cf_screened` 表示所声明范围的 CF 检查完成，均不保证完全非 CF 或未来可用。未完成检查的手动结果列出分项及失败原因，默认建议重测/更换；强制采用须两次确认，不能绕过 TLS 验证或明确 CF 拒绝。官方 IP 段或 DNS 解析失败仍直接停止，无法强制跳过；旧 selection 仍可读取，单一布尔值不授权自动推荐。

新记录的 `target_mode` 为 `pinned_ip`（原域名固定 IP）、`literal_ip`（原输入 IP）或 `dynamic_dns`，同时保存 `dynamic_dns`、`original_host`、`target_port`、`selected_ip`、`selected_family`、`tested_target`、`checked_at`。`target` 总是实际运行目标；动态模式的 `tested_target` 仍为本次验证的 IP，不能据此声称运行时始终使用该 IP。旧记录不增加或推测这些字段，`keep` 保留原 target、SNI、UUID、shortId 和密钥（除非用户另行明确选择更换）。

菜单 8 → 5 对当前目标复查，target、SNI 和原始目标域名分开处理：只有与当前目标/SNI 匹配的 selection.requested_target 域名才用于 DNS 漂移；任一 A/AAAA 仍匹配固定 IP 就不报漂移，IPv6 使用规范地址值比较。原始 IP 不适用 DNS 漂移；明确动态模式显示当前 DNS 以及本次抽测结果，dns_drift 保持空值，说明未来运行 IP 未固定。缺记录、旧域名未固定、记录失配或 DNS 失败显示未知，不以 SNI 推测，也不自动换 IP。网络复查只写独立诊断记录，不改节点或重启服务；目标/SNI/选站记录变化后不沿用旧健康诊断。

菜单 8 的本机 Reality 客户端检测使用临时已校验 Xray、正常 HTTPS 验证并读取返回数据，避免把原生配置检查当作连接成功。它没有经过外部防火墙/云安全组，结果始终说明公网未验证。认证失败与目标握手失败在部分内核日志中无法可靠区分，按组合/未知原因报告，不猜测通过。
