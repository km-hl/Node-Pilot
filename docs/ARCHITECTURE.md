# NodePilot 第一阶段架构

仅支持 Reality / AnyTLS / HY2。Bash 只负责可信引导，Python 3.10+ 标准库负责交互、校验和事务；不运行管理后台、不驻留 Python、不引入 pip 依赖。

## 目录

```text
install.sh                  系统检查、依赖、项目安装、自动进入菜单
nodepilot                   Python 入口
nodepilot_pkg/
  common.py                 私有文件、原子写入、命令边界
  model.py                  schema=1 元数据、输入约束
  config.py / share.py      纯配置生成、分享链接与 Mihomo/二维码适配
  exporters.py / qr.py      Mihomo 片段与完整离线配置、纯 Python 终端二维码
  system.py / download.py   OS、端口、公网 IP、只读防火墙参考与校验下载
  cores.py                  官方内核、版本与架构检查
  certificates.py           证书导入、自签名、certbot HTTP-01
  caddy.py                  自建 Reality 目标、共享 Caddy、配置校验与重载
  targets.py                从本 VPS 排除 CF、有效证书/协议检查、时延排名
  services.py / manager.py  独立服务、修订和回滚
  menu.py / cli.py           TTY 交互与离线检查
tests/                      单元、故障注入、官方内核检查
docs/                       规范、验证记录、VPS 验收清单
```

运行时：`/etc/nodepilot/nodes/<随机ID>/revisions/<随机修订>/` 同时保存 `node.json`、`config.json` 和本项目证书。`current` 是原子切换的符号链接，配置和元数据一起切换。修改前保存私有备份；待处理事务写入磁盘，中断后恢复旧修订。`/opt/nodepilot/cores/` 保存独立校验内核；服务名 `nodepilot-n-<16位随机十六进制>.service`。

## 元数据

通用字段：schema、id、name、protocol、core、transport、listen、port、server（分享地址）、sni、service、created_at。

Reality：reality.uuid、private_key、public_key、short_id、server_names、target、fingerprint。私钥仅用于服务端配置，分享只含公钥。TLS：password、tls.mode / certificate / key；HY2 另可含 obfs、up_mbps、down_mbps。

Reality 新增 reality.mode（self / external；旧记录缺省 external）。0.6.0 起彻底移除 Xray 内核，所有协议统一使用 `core="sing-box"`（Reality 配置 `tls.reality.handshake` 与 `max_time_difference="1m"`），`self` 使用 caddy.domain / port / certificate_mode（acme / dns01 / import / selfsigned）/ email（可选）/ upstream（可选）及证书路径；target 必须精确为本机 127.0.0.1:caddy.port，SNI、domain 与唯一 serverNames 相同且不再启用 `proxy_protocol`。external 的 reality.selection 记录测量时间、中位时延、波动与可选 `mlkem768` 探测，不代表长期可用或真实联通证明。HY2 可选 `mport`（端口跳跃范围）与 `hop_interval`。schema 保持 1。

所有运行时目录 0700、文件 0600；元数据包含认证秘密，不是公开节点清单。公网 IP 探测失败时手动输入，不回退到本机私网地址。端口冲突按 TCP/UDP 区分；TCP 与 UDP 443 可以共存。

## 官方规范核对（2026-10-14）

- [sing-box VLESS Reality](https://sing-box.sagernet.org/configuration/inbound/vless/) 与 [TLS Reality](https://sing-box.sagernet.org/configuration/shared/tls/#reality)：`tls.reality.handshake`、`private_key`、`short_id`、`max_time_difference`；Vision 客户端 flow `xtls-rprx-vision`。SNI 必须被目标 TLS 站点接受，探测 TLS 1.3 和 h2，仅通过配置检查不能证明 Reality 可连接。
- [sing-box AnyTLS](https://sing-box.sagernet.org/configuration/inbound/anytls/)：1.12.0 起支持，users[].password + TLS。
- [sing-box HY2](https://sing-box.sagernet.org/configuration/inbound/hysteria2/)：UDP/QUIC，users[].password、TLS、可选 salamander，带宽提示不代表强制链路限速。
- [sing-box TLS](https://sing-box.sagernet.org/configuration/shared/tls/)：本地 certificate_path / key_path。新版 inline ACME 已废弃，使用系统 certbot 单独申请，避免依赖被移除的字段。
- [AnyTLS URI](https://github.com/anytls/anytls-go/blob/main/docs/uri_scheme.md)、[HY2 URI](https://v2.hysteria.network/docs/developers/URI-Scheme/)：密码放入百分号编码 auth，IPv6 加方括号，SNI / 混淆放 query，名称放 fragment；不把带宽写入 URI。

已阅读需求指定的 JollyRoger 380 / 457 / 210 教程用于流程参考，未复制其脚本。361 是 XHTTP，本阶段排除。安装时动态读取官方最新稳定版 `sing-box` 与 `caddy` 并校验 SHA-256，不假设网页示例与所有历史版本通用。

## 小内存原则

内核使用官方预编译文件，不在 VPS 上编译。下载流式写盘、仅提取所需二进制、不下载 geodata。0.5.0 新建节点统一默认 `sing-box` 单内核，减少多内核冗余内存占用并注入 `GOMEMLIMIT=64MiB`、`GOGC=50`、`GOMAXPROCS=1`、`GODEBUG=multipathtcp=0` 以及 systemd cgroup 边界（节点单元 `MemoryHigh=72M`/`MemoryMax=96M`，共享 Caddy `48M`/`64M`）。`GOMEMLIMIT` 与 `MemoryHigh` 仅是软目标或回收阈值，绝不声称系统整机已满足硬限制。256MiB Debian 13 cgroup（4 进程冷 inode 并发 12MiB）测得实际 `Pss=135.76MiB`、峰值 `184.09MiB`（`max=0` / `oom=0`），仍未替代完整物理 256MiB 整机长期及 ACME 峰值验证；不自动修改 swap/sysctl/IPv6 或防火墙规则。

## 版本规范复核（2026-11-14）

- 官方内核复测版本：`sing-box v1.14.4`、`Caddy v2.11.9`、`Mihomo v1.19.34`（Debian 13 amd64）。
- Reality（sing-box）与 AnyTLS、HY2 原生检查均通过 `sing-box check -c`；Mihomo `-t` 与本地 `--live` 回环检查通过 `packet-encoding: xudp`、`support-x25519mlkem768: true` 与 HY2 `ports`/`hop-interval`。
- Certbot 4.0.0 在 `/proc/sys/net/ipv6/bindv6only == 0` 下通过 `HTTP01DualNetworkedServers` 支持双栈 HTTP-01（省略 `--http-01-address`）。
