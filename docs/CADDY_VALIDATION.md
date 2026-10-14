# v0.2.0 Caddy 与选站验证

日期：2026-10-14。真实测试机为 Debian 13 amd64、1CPU/约 2GiB RAM；不是物理 256MiB VPS。所有测试凭据、PEM、URI、服务器地址、节点 ID、SSH 主机指纹和原始日志留在忽略的本地目录或 VPS 私有目录，未进入本报告。

## 已通过

- Windows 70 项测试（26 项 Linux/openssl 环境检查跳过）；Debian WSL 与真实 Debian VPS 70 项全部通过。覆盖默认域名流程、无域名排名、自定义 target/端口、邮箱可选、CF/跳转/TLS 拒绝、多节点、共享配置回滚、删除失败恢复、证书备份恢复与旧节点兼容。
- bash -n、shellcheck 0.10.0 通过；官方已校验 Xray 26.3.27、sing-box 1.14.3、Caddy 2.11.7 的三协议、self Reality、Caddy import/ACME 配置原生检查通过。ACME 配置校验不等于证书签发。
- 在 VPS 创建两个自建 Reality 节点，共用独立 NodePilot Caddy，两个 loopback 目标；测试证书显式生成并导入，健康检查正常校验 SAN/TLS 1.3/h2，Xray xver=1。
- 两个节点分别经 VPS 外部 sing-box 与 Xray 客户端完成 1MiB 数据完整性和 HTTPS 检查；sing-box 各 4 次传输、最多 2 并发。Reality 使用公钥认证，不以跳过证书验证替代认证。
- 修改 SNI/端口/证书后原生检查及联通通过；内核检查后真实端口抢占导致启动失败时，节点修订及共享 Caddy 回滚。删除一个不影响另一个；删除最后一个时共享单元停止；证书私有备份恢复后两个目标重新就绪。
- Caddy 内核候选经过原生校验后，真实本机目标端口抢占令新服务启动失败，内核 current 指针回滚到旧版本、三节点仍 active、本机 TLS 就绪。随后同一已校验候选成功切换，节点元数据保持完整。
- VPS 测量候选：一次样本 www.bing.com 中位 10.02ms、www.apple.com 12.03ms、www.google.com 13.05ms。Microsoft/Oracle 的异常页面、Samsung/dl.google 的跳转及 Amazon 的不合格连接被排除。这些结果会随 VPS/时间/DNS 变化，不是固定推荐名单。
- speed.cloudflare.com 被官方 CF IP 段检查排除。官方 www IP 文本列表在该 VPS 返回 403，官方公开 API 正常返回 15 个 IPv4、7 个 IPv6 段；实现优先 API，文本为备用，全部失败时安全中止。
- 按测量选出的 www.bing.com 第三方 Reality 目标，在外部 sing-box 客户端完成 2MiB 完整性及公网 HTTPS 检查。选站预检没有替代此真实客户端测试。

## 小内存短测

把三个测试节点切换到单独的 systemd 测试组，**一核 CPUQuota=100%、MemoryMax=192MiB、MemorySwapMax=0**。测试组内复制校验内核至新 inode，避免预热代码页在其他 cgroup 计账，运行 Xray、两个 sing-box、共享 Caddy 和采样 Python。

三协议各 8 次 1MiB 传输、最多 4 并发，共 24MiB，并分别验证服务端 DNS/外网出站/HTTPS。全部通过。180 秒、355 次采样已完整结束，无 OOM/oom_kill、无 swap；峰值 memory.current 达 201326592 字节，合计 RSS 达 187360KiB。

**达到硬上限，memory.events max=322，发生回收压力；不能将 192MiB 当作未受限的自然内存峰值。** 此测试不计完整操作系统、SSH、安装依赖及 ACME 签发/续期，不能宣称完整 256MiB 实机长期可用。相比旧版三进程，新流程的额外 Caddy 消耗已实际纳入测试。

## 待验证

用户尚未提供域名：真实 ACME 签发与临期自动续期未执行。自建目标目前用明确标记的测试证书验证，而非冒充公网可信证书。Ubuntu/arm64、GUI 客户端矩阵、真实 256MiB 整机和长时间 QUIC/ACME 并发仍待测试。

NodePilot 管理器无常驻进程；共享 Caddy 仅在有自建目标时运行。删除最后一个自建 Reality、停止共享 Caddy 后，AnyTLS/HY2 再次通过外部数据与 HTTPS 检查。所有本次临时节点、单元、payload 及失败测试目录已清理；系统原有 x-ui 仍 active，原配置 SHA-256 与测试前一致，正式 NodePilot 节点集合仍为空。
