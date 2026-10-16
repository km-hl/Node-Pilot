# v0.4.0 审查与实施记录

基准：548e842 / v0.3.0。2026-10-14 核对本地 HEAD、远端 main 和 CI 均一致；Linux 95 项测试全部通过。

## 已确认的问题

1. certificates.acme 和 Caddy.preflight 无条件绑定 IPv6 TCP 80；IPv6 内核不可用时会误拒绝合法 IPv4 HTTP-01。
2. certificates.prepare 每次修改 ACME 节点均执行签发，并在证书复用前检查 DNS/端口；影响已有有效证书的日常修改。
3. 卸载逐节点删除，中途失败没有整体持久化恢复进度。
4. Reality 扫描未读取协商密钥组、每地址族只测第一个 IP，HTTP 跳转作为硬失败，无法完整解释其他地址和质量指标。
5. 服务启动只观察两秒 active，无监听、退出结果及重启次数校验。
6. 尚无 DNS-01、Mihomo 导出、证书到期与续期状态展示、修改确认摘要。

## 实施顺序与约束

- 阶段 1：按域名实际 A/AAAA 和本机可用地址族检查 HTTP-01；保留严格 DNS 指向验证。卸载先预检所有对象和备份，保留进度、回滚服务，明确最终文件清理的恢复边界。
- 阶段 2：更换默认候选池，保留手动目标。使用 OpenSSL s_client 验证证书/SNI、TLS 1.3、h2 和实际 X25519 组；每地址至少三次、分族、多地址失败继续，HTTP 状态作为质量信息，CF 继续排除。
- 阶段 3：复用有效证书、临期续期、强制续期和私有状态记录。Cloudflare DNS-01 使用发行版 certbot DNS 插件处理 TXT 创建、传播等待及清理；凭据单独保存 0600，不进入节点链接、命令行或公开单元。Zone ID 做域名归属验证，Caddy 加载独立签发的 PEM。
- 阶段 4：服务实际监听与重启诊断、列表信息、修改摘要和确认、Mihomo YAML 导出、可选本机 Reality HTTPS 客户端验证。公网可达性与本机成功分开显示。
- 阶段 5：逐修复测试、原生检查、真实 VPS 临时服务回归、受限 cgroup 资源矩阵、文档和 Release 准备；上传前检查公开 Git blob、运行包和隐私信息。

不改全局防火墙、IPv6、路由、sysctl 或 swap；生产 x-ui、Caddy/Nginx 不参与破坏性测试。v0.3.0 schema=1 及单元内容保持兼容。没有真实域名/Cloudflare 凭据时，DNS-01 只报告模块与故障测试，真实签发保留待验证项；cgroup 限制不等同于完整小内存实机验收。

## 研究依据

[3x-ui Reality 扫描](https://github.com/MHSanaei/3x-ui/blob/master/internal/web/service/reality_scan.go) 将 TLS 1.3、h2、X25519 相关组和可信证书匹配作为可行性条件；本项目独立实现，保持内置证书校验。HTTP 页面质量与上述 TLS 条件分开。

[OpenSSL s_client](https://docs.openssl.org/3.0/man1/openssl-s_client/) 提供组、ALPN、主机名与验证错误控制；标准库缺少可靠的协商组接口时用有界子进程取得实际结果，不根据客户端提供的组猜测通过。

[certbot Cloudflare 插件](https://certbot-dns-cloudflare.readthedocs.io/en/stable/) 使用私有 INI Token，自动添加/删除 TXT 并等待传播。使用限定 Zone 的 Token，不支持 Global API Key；未提供凭据时不尝试真实签发。

[Mihomo VLESS](https://wiki.metacubex.one/config/proxies/vless/)、[AnyTLS](https://wiki.metacubex.one/config/proxies/anytls/)、[Hysteria2](https://wiki.metacubex.one/config/proxies/hysteria2/) 用于核对导出字段；客户端配置解析测试和 GUI 实测分开报告。
