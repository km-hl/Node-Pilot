# Changelog

## 0.4.0 — 候选，尚未稳定发布

RC3 增量：

- 升级分别保存并恢复续期 timer 的 UnitFileState / ActiveState；保留 enabled-runtime、disabled + active 和 enabled + inactive，首次安装才默认启用并启动。拒绝 masked、未知/转换状态、外部 FragmentPath 和 drop-in。
- 程序文件复制前持久化原修订/版本、launcher、原单元字节、timer 状态、owner、源配置、目标文件 SHA-256 及阶段。回滚失败保留 rolling_back 事务，重试继续回滚；候选或事务损坏时保留证据、拒绝执行。
- 兼容 RC2 的合法旧日志，保留原始证据和恢复时可观察到的 timer 策略；旧日志没有记录的升级前状态无法事后还原。
- owner 迁移移到获取状态锁后；候选首次执行前校验完整文件清单，已有同摘要目录也必须匹配已验证 payload。卸载显式清理 runtime timer 链接。
- 新增六状态成功/失败/中断矩阵、准备阶段与 timer 操作中断、安全拒绝回归，以及可销毁环境真实 systemd smoke 脚本。详见 [RC3 验证与限制](docs/RC_THREE_VALIDATION.md)。

RC2 增量：

- Caddy 显式分离 tcp4/tcp6，校验监听 PID 与地址族；Certbot standalone 双栈明确拒绝并提示 DNS-01，不依赖 IPv6 通配覆盖 IPv4。
- Token 先校验格式、临时存储并验证 Zone，确认部署且服务通过后登记；失败/取消/中断回收未提交凭据。支持复用 ID、共享引用保护及确认清理孤立凭据，损坏或未知 schema 时拒绝清理。
- DNS 签发中断给插件 TXT 清理宽限期；API 故障/断电仍可能留下记录，真实 DNS-01 验收未完成。PEM 恢复移除 DNS 绑定，必须重新绑定 Token。
- 程序升级增加持久事务、候选文件 SHA-256 和重复恢复；所有权 schema=2 阻止旧管理器误操作。节点目录外的本项目事务日志支持删除续做，Caddy 节点/内核回滚恢复原配置字节。新增开机 timer 检查。
- 安装器使用隔离 Python 入口，只执行已校验 payload；新增当前目录同名包干扰测试。
- Reality 手动复查固定 IP、TLS 条件和 DNS 差异，不自动更换参数；不把未知/混合密钥组冒充经典 X25519。菜单展示临期、续期失败、凭据缺失和可用内存。
- 详见 [RC2 验证与未完成事项](docs/RC_TWO_VALIDATION.md)。保持草稿 PR，不合并、不创建稳定 Release。

- HTTP-01 按域名实际地址族检查 DNS/端口，兼容 IPv4-only/IPv6 内核禁用；Caddy 监听相应地址族。
- certbot 证书有效且 SNI 未变时复用，临期续期，支持手动强制；失败保持旧修订。新增证书详情、自动续期运行状态和私有记录。
- Cloudflare DNS-01：发行版 certbot 插件、限定 Zone Token、Zone ID 检查、传播等待、私有凭据、日志前脱敏及 SDK 配置隔离。官方 Caddy 直接加载独立 PEM；真实签发待域名/Token 验证。
- 卸载增加全部预检、备份、持久化进度、服务阶段回滚和最终清理续做；保留恢复边界，不声称原子卸载。
- Reality 默认池去除 Apple/Google，保留手动目标和旧节点。实际 OpenSSL X25519/TLS1.3/h2/证书检测，多地址三次测量；网页跳转/HTTP 状态转为质量警告，CF 检测继续生效。
- 服务诊断检查 Result、退出/重启次数及主进程真实监听；新增本机 Reality 代理 HTTPS 测试，保持公网未验证标记。
- 修改前摘要/确认，节点详情、Mihomo YAML 导出；Mihomo 原生解析验证通过，GUI 矩阵未验证。
- 12 组 256/512/1024MiB、1 CPU cgroup 短测全部完成；256MiB 三协议触及限制。完整低内存实机、真实 ACME 与长期负载仍未验收。

## 0.3.0

y/n 大小写确认、稳定版本选择和只读更新检查；独立 Xray/sing-box 接管/归还、原文件备份、中断/启动失败恢复。

## 0.2.0

自有域名 + 共享 Caddy 本机 Reality 目标，非 CF 第三方选站及受限资源测试。

## 0.1.0

三协议、独立服务、修订/回滚、TLS、分享、安装与 SSH 终端管理入口。
