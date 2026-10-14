# 第一阶段本地验证记录

日期：2026-10-14。目标版本：NodePilot 0.1.0。本文记录首次开发的本地验证，Linux 环境为 Debian 13 amd64 WSL（systemd）；后续真实 VPS 联通与性能结果见 [VPS 测试报告](VPS_TEST_REPORT.md)。初次本地阶段不包含公网节点。

## 按开发顺序的结果

| 阶段 | 已完成 | 实际验证 | 未验证事项 |
|---|---|---|---|
| 1 模型/规范 | 模块结构、schema=1、三协议配置、URI | 初始 9 项配置/分享单测通过；官方文档核对 | 各客户端实际导入 |
| 2 安装/菜单 | root/系统/架构检查、按需依赖、项目校验安装、TTY菜单 | Debian 13 WSL：首次安装自动菜单、重复安装、源码升级、错误输入循环、stdin 管道，全部通过并清理 | 干净 Ubuntu、arm64、发布 URL 下载安装、缺依赖的完整 apt 安装路径 |
| 3 Reality | UUID/x25519/shortId、目标探测、Vision配置、编辑删除分享 | 官方 Xray v26.3.27 原生检查；systemd loopback TCP监听；实际重启失败回滚 | 实际 target 的完整公网 Reality 握手与客户端连接 |
| 4 AnyTLS/HY2/TLS | sing-box、证书导入/自签名/HTTP-01、续期timer、混淆/带宽 | 官方 sing-box v1.14.3 原生检查；真实自签名生成；两个服务实际启动与TCP/UDP监听 | ACME公网签发、临期续期、UDP公网可达性、客户端业务流量 |
| 5 多实例/生命周期 | 独立服务、锁、修订切换、中断恢复、备份恢复、内核和项目升级、保守卸载 | 三节点共存，TCP/UDP同端口，删单节点隔离；故障注入；安装测试清理；文件清单保留外部文件测试 | 真实发行版间迁移、在线脚本升级、内核升级失败的真实长跑 |
| 6 测试/检查 | 标准库测试、静态检查、测试工具、CI工作流、中文文档 | 初次44项，补充隐私和现有内核检测后50项 Linux 测试全部通过；bash -n、shellcheck、真实内核检查通过 | 独立第三方审计、完整小内存实机长测 |

## 命令与证据

```text
python3 -m unittest discover -s tests -v
Ran 50 tests ... OK（当前源码；首次为44项）

bash -n install.sh
shellcheck install.sh
均返回 0，无诊断

python3 tools/native_check.py --download
xray 26.3.27 SHA-256/ELF/version OK
sing-box 1.14.3 SHA-256/ELF/version OK
reality official native config check OK
anytls official native config check OK
hysteria2 official native config check OK

python3 tools/install_smoke.py
local install automatically opens TTY menu OK
repeat install preserves state OK
local script upgrade preserves state OK
invalid menu input loops OK
stdin pipe retains interactive TTY OK
temporary installation, timer and private test state cleaned

python3 tools/service_smoke.py
三节点分别 systemd active，Reality TCP 与 HY2 UDP 使用同一个随机端口号
real restart failure rollback OK
single-node deletion isolation OK
temporary test nodes and units cleaned
```

正式内核压缩包校验值：Xray Linux amd64 `23cd9af937744d97776ee35ecad4972cf4b2109d1e0fe6be9930467608f7c8ae`；sing-box Linux amd64 `e2bdf179c15a3652955dc44867e33ca0965c9c9b91b34267dcc5a5d639a5feee`。来自本次官方 API digest，下载后已实际验证。安装器未来稳定版会动态查询，不固定为这些历史版本。

Windows 的文件锁/符号链接管理测试会明确跳过；上述全部通过指 Linux 执行结果，不把 Windows 跳过算作通过。

## 实测发现并修复

1. 新版 Xray x25519 的公钥输出是 `Password (PublicKey)`，修复解析并覆盖旧 Public key / Password 标签。
2. systemd 的地址族限制必须包含 AF_NETLINK，否则 sing-box 启动网络初始化会退出。已放行其只读网络接口查询需要的地址族，不授予修改全局网络的能力。
3. Python 用 TextIO `r+` 打开终端可能因不可 seek 失败。已使用独立 /dev/tty 读、写句柄，真实 PTY与管道测试通过。
4. 卸载不再递归删除整个程序根目录，改为文件清单删除，未知外部添加文件会保留；测试覆盖。
5. 导入 Reality 私钥时通过 OpenSSL 二进制 stdin 派生公钥，避免在进程命令行暴露私钥；已与官方 Xray 输出交叉检查。

## 性能证据与限制

第一次未固定 GOMAXPROCS 的 WSL 空闲观察：三进程合计 RSS 133104 KiB（约130MiB）。最终服务固定 `GOMAXPROCS=1` 后重跑：Reality 30208 KiB、AnyTLS 46632 KiB、HY2 47512 KiB，合计 124352 KiB（约122MiB）。进程内存包含代码、堆、线程与共享页，RSS不能直接等价于物理内存独占量。

**这些是 WSL 的短期空闲观察，不是 1c/256MiB VPS 压测。** 管理器无后台进程、无需 pip、流式下载、不编译内核，符合低开销方向；256MiB 在活跃 QUIC、多节点、续期时是否足够仍必须按 [VPS清单](VPS_ACCEPTANCE.md) 实测。GOMEMLIMIT 是软目标，不承诺 RSS 硬限制。

## 已知限制 / 下一步

- 初次本地验证尚无发布源，后续已绑定本仓库；公开发布/在线升级结果见 VPS 报告和仓库实际 Release。
- 只支持 HTTP-01，不支持 DNS-01；要求 A/AAAA 与公网探测一致，探测失败时先使用证书导入路径。外部防火墙/云安全组不能自动证明放行。
- 自签名 URI 保持安全验证，需要客户端手动导入证书；客户端协议/URI支持取决于版本，目前没有兼容性实测矩阵。
- 备份恢复仅允许空节点集，证书恢复为导入模式，ACME账户不迁移；历史修订、旧内核和备份不自动轮转。
- 原生配置检查和两秒服务 active 检查不等于持续健康或公网连接证明。
- 后续三协议客户端外网、HTTPS、生命周期与限额测试已完成；ACME、arm64、Ubuntu 和完整 256MiB 长测仍待补充。
