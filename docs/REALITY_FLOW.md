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
- DNS 查询有独立进程超时，拒绝非公网地址；任意 DNS 地址落在 CF IP 段、规范名称有 CF 标记即排除。仅使用 Cloudflare 权威 DNS 不等于使用其 CDN，不按 NS 一刀切排除。
- OpenSSL s_client 保留可信链和主机名验证，显式要求 TLS1.3/h2 并提供 X25519，再读取实际协商组；没有可靠组结果标为未验证。每地址三次测量，取中位数和极差，测量包含子进程开销。HTTP 请求固定在筛选 IP，不跟随跳转；CF 响应标记仍排除，HTTP 不可用会显示未验证质量项。
- 最多测试每族三个地址，交错检测 IPv4/IPv6，一个失败继续其他地址；超过上限明确未测。自动选择固定到已验证的 IP，避免内核重新解析时拨到失败地址；手动 target 保留用户输入。DNS/CDN 地址会变化，只声称排除了当前检测到的 CF，不能保证未来地址。旧节点升级和目标未变的编辑不强制重选。
- 名单测量来自 VPS 到目标，不能用开发者电脑的 ping 代替。低握手时延不代表高吞吐，也不能证明 Reality 真正握手成功；必须从真实客户端再验证。

独立重测命令（在 VPS 中运行）：

```bash
nodepilot targets
nodepilot targets --domains www.amazon.com,aws.amazon.com,www.samsung.com
```

已有节点修改可输入 `keep` 保留原第三方目标；目标/SNI 未变时不重新筛选，变更摘要须确认。自建转第三方输入 `-`；反向切换填写自己的域名。证书失败不静默改用第三方 SNI。

输入 `manual` 可指定 target 域名/IP、端口及客户端 SNI，执行当前 CF、可信证书、TLS1.3/h2/X25519 与三次检测，网页质量单独提示。选择域名 target 时，内核可能重新解析到其他地址；用户需根据分地址结果评估。

菜单 8 的本机 Reality 客户端检测使用临时已校验 Xray、正常 HTTPS 验证并读取返回数据，避免把原生配置检查当作连接成功。它没有经过外部防火墙/云安全组，结果始终说明公网未验证。认证失败与目标握手失败在部分内核日志中无法可靠区分，按组合/未知原因报告，不猜测通过。
