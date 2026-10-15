# Changelog

## 0.4.0 — 候选，尚未稳定发布

RC4 收尾（不另建 RC5）：

- 修复 complete 跨重启事务边界：子版本 3 保存开始/提交 boot_id、timer 策略及启用链接证据。同次启动严格比较；跨重启核对持久链接、runtime 消失及 systemd 首次激活时间，保守识别开机变化，不主动改变 timer。兼容 RC2/子版本 1/2，无历史证据不猜测。timer 单独需要人工核对时支持精确日志 SHA-256 的只读最终化，仍强制所有程序/来源校验。
- 修复 bind-source 事务隔离：入口自行加全局锁，联网前后只读检查全部活动管理事务；合法卸载/接管终态与备份审计历史放行，活动、损坏、未知及链接记录拒绝，不自动卸载/删除/接管，也不迁移 owner 或重启节点。检测联网期间 source.json 外部变更并拒绝覆盖。
- 附加事务审查修复：complete 已落盘后报告/同步错误不再触发反向回滚，保留日志以只读重试；只读入口不初始化缺失状态/辅助目录，锁拒绝外部符号链接及硬链接且不在获锁前迁移 owner。来源 replace 后报错时按精确字节回退旧记录/原缺失状态；回退失败明确报错，不覆盖外部编辑/链接。增加六状态跨重启、人工恢复、双进程锁竞争及文件不变故障回归。当前结果与整机重启/真实 ACME 等缺口见 RC4 报告最新节。
- complete 阶段新增独立只读最终化：核对程序/哈希、owner、launcher、单元、timer、来源后仅删除活动日志，不重装、重写、reload、self-check 或恢复 timer；不一致和删除 I/O 错误保留证据。
- 本地打包不再向部署传递模板 Release URL；source.json 区分 official-release / local-source / explicit-user-bound。来源变化需确认，Release 转本地保留非活动历史绑定；旧合法 repository 格式兼容读取，不推断 Git remote。
- 新增显式 bind-source --confirm，仅在稳定 Release 附件来源/SHA 元数据通过后绑定；未绑定、损坏、未知来源或活动事务拒绝在线更新/绑定。沿用原下载、解包、候选执行前校验和原配置回滚。
- 来源感知事务使用子版本 2，继续恢复旧子版本 1；旧 RC3/初版 RC4 将明确拒绝新事务，避免忽略来源字段后错误恢复绑定。外层 owner/schema 和节点格式不变。
- 增加 complete 异常/重复/删除失败、来源授权迁移矩阵，以及真实 PTY/systemd 的 complete 最终化和修改版 Fork 本地安装测试。外部 ACME/IPv6/重启/低内存整机/GUI 验收限制继续保留。

RC4 增量：

- 安装器所有非升级入口在进入旧菜单前检查活动 program-pending.json，包括损坏、目录、正常/悬空符号链接；保守拒绝并指引可信新版 --local --upgrade，不将文件存在视为有效事务。历史 program-pending-rc2.json 不阻止正常启动。
- 重用已校验 payload 的恢复状态机；恢复后若目标包已完整安装，重复升级不再创建备份、重写单元或改变 timer。旧修订缺失/入口或包目录链接、必要候选模块缺失、记录与实际版本不符均拒绝并保留证据；日志限定为普通文件及 256KiB。
- 菜单与续期入口先在锁内验证/恢复事务，再迁移 owner，避免拒绝异常事务前改变原标记。未追溯修改已安装的旧 v0.3 入口，遇到新事务仍需可信新版安装器。
- 新增安装器真实 Bash 隔离回归与可销毁 Linux PTY/systemd 验收：v0.3/RC1/RC2/RC3 到 RC4、准备/切换中断、异常日志拒绝、重复恢复和历史证据放行。详见 [RC4 验证报告](docs/RC_FOUR_VALIDATION.md)。DNS-01 真实签发及续期仍等待授权测试环境，保持草稿候选。

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
