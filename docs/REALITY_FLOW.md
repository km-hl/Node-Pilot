# Reality 默认流程与第三方选站

v0.2.0 默认参考 [Reality 偷自己 / Caddy 管理证书教程](https://jollyroger.top/sites/384.html)，重新实现独立服务和安全事务，未复制其脚本。

## 有域名：默认自建目标

1. 用户提供自己控制的域名，全部 A/AAAA 直指 VPS。SNI、Xray serverNames 和证书域名使用同一值。
2. Xray 在用户选择的公网 TCP 端口监听 VLESS/Reality/Vision；本机目标为 `127.0.0.1:<独立端口>`、xver=1。
3. 独立 `nodepilot-caddy.service` 聚合全部自建目标，每个节点一个 loopback TLS 监听。PROXY wrapper 仅信任 127.0.0.1；不开放默认 2019 管理接口，管理器通过 root 私有 Unix socket 重载配置。
4. 默认由 Caddy 使用 Let's Encrypt HTTP-01 自动申请及续期，邮箱可选。HTTP-01 共享公网 TCP 80，TLS-ALPN 挑战关闭，避免抢占 Xray 的公网 TCP 443。已有其他软件占用 TCP 80 时安全中止，用户可以导入现有证书；不停止其他网站。
5. Caddy 默认返回简单欢迎页，也可反向代理用户指定的 HTTPS 域名；网页来源不是 SNI。关闭 Caddy HTTP/3，避免增加资源及占用 HY2 的 UDP 端口。
6. Xray 原生检查、Caddy 原生检查、聚合配置重载及本机证书/TLS 1.3/h2 就绪后才报告创建成功。任何一步失败恢复旧修订和聚合配置。多个自建节点共用一个 Caddy；删除一个仅去掉其监听，最后一个删除时停止本项目 Caddy。

[Caddy 自动 HTTPS](https://caddyserver.com/docs/automatic-https)、[PROXY wrapper](https://caddyserver.com/docs/caddyfile/options#proxy_protocol)、[官方 ACME 邮箱可选定义](https://github.com/caddyserver/caddy/blob/master/modules/caddytls/acmeissuer.go) 支持以上配置。导入的测试证书只用于验证流程，不能当作公网可信 ACME 证书。

自建目标备份包含证书及私钥，恢复为导入模式；ACME 账户没有被迁移，需重新选择自动证书。Caddy storage 在 `/etc/nodepilot/caddy/`，删除/卸载保留私有历史证书和备份；不操作系统 Caddy 文件。

## 没有域名：从本 VPS 筛选第三方目标

[REALITY 官方说明](https://github.com/XTLS/REALITY/blob/main/README.en.md) 要求目标支持 TLS 1.3、HTTP/2，所用域名不发生跳转，并建议目标 IP 靠近代理服务器。本站排名把目标选择转为可重测的网络结果，不能保证抗封锁或客户端端到端时延。

- 内置域名仅是候选，也可输入 1–12 个自选候选。不把 Apple、Microsoft 或任何热门域名永久认作可用。
- 从 [Cloudflare 官方公开 API](https://developers.cloudflare.com/api/resources/ips/methods/list/) 获取当前 IPv4/IPv6 网段，失败时尝试官方文本列表。全部来源失败则停止选站，避免将未知结果当作非 CF。
- DNS 查询有独立进程超时，拒绝非公网地址；任意 DNS 地址落在 CF IP 段、规范名称有 CF 标记即排除。仅使用 Cloudflare 权威 DNS 不等于使用其 CDN，不按 NS 一刀切排除。
- 在可测试的 IPv4/IPv6 地址上，正常校验证书与 SNI，要求 TLS 1.3/h2，连续三次测 TCP+TLS 握手，以中位时延、再以波动排序。检测到 `cf-ray`、`cf-cache-status`、`Server: cloudflare` 即排除；根页面跳转、异常响应也排除。HTTP 检查固定到已经筛选的 IP，不跟随重定向。
- 最多测试每个域名每族一个地址；DNS 地址会变化，CF 也可能使用其他基础设施，所以只能声称排除了当前检测到的 CF，不能证明所有未来地址均不是 CF。安装/修改再次筛选，旧节点升级不强制改写。
- 名单测量来自 VPS 到目标，不能用开发者电脑的 ping 代替。低握手时延不代表高吞吐，也不能证明 Reality 真正握手成功；必须从真实客户端再验证。

独立重测命令（在 VPS 中运行）：

```bash
nodepilot targets
nodepilot targets --domains www.apple.com,www.bing.com,www.google.com
```

已有节点修改可输入 `keep` 保留原第三方目标，但保存时仍执行当前非 CF 筛选。自建目标切换到第三方时，域名输入 `-`；反向切换时填写自己的域名。证书申请失败不会静默改用第三方 SNI。

输入 `manual` 可自定义第三方 target 的域名/IP、端口及客户端 SNI，保留原有自定义能力；仍执行 CF 排除、证书、协议、网页及三次时延检查，不提供跳过筛选的默认入口。
