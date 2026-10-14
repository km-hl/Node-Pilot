# NodePilot 第一阶段架构

仅支持 Reality / AnyTLS / HY2。Bash 只负责可信引导，Python 3.10+ 标准库负责交互、校验和事务；不运行管理后台、不驻留 Python、不引入 pip 依赖。

## 目录

```text
install.sh                  系统检查、依赖、项目安装、自动进入菜单
nodepilot                   Python 入口
nodepilot_pkg/
  common.py                 私有文件、原子写入、命令边界
  model.py                  schema=1 元数据、输入约束
  config.py / share.py      纯配置生成和分享链接
  system.py / download.py   OS、端口、公网 IP、校验下载
  cores.py                  官方内核、版本与架构检查
  certificates.py           证书导入、自签名、certbot HTTP-01
  services.py / manager.py  独立服务、修订和回滚
  menu.py / cli.py           TTY 交互与离线检查
tests/                      单元、故障注入、官方内核检查
docs/                       规范、验证记录、VPS 验收清单
```

运行时：`/etc/nodepilot/nodes/<随机ID>/revisions/<随机修订>/` 同时保存 `node.json`、`config.json` 和本项目证书。`current` 是原子切换的符号链接，配置和元数据一起切换。修改前保存私有备份；待处理事务写入磁盘，中断后恢复旧修订。`/opt/nodepilot/cores/` 保存独立校验内核；服务名 `nodepilot-n-<16位随机十六进制>.service`。

## 元数据

通用字段：schema、id、name、protocol、core、transport、listen、port、server（分享地址）、sni、service、created_at。

Reality：reality.uuid、private_key、public_key、short_id、server_names、target、fingerprint。私钥仅用于服务端配置，分享只含公钥。TLS：password、tls.mode / certificate / key；HY2 另可含 obfs、up_mbps、down_mbps。

所有运行时目录 0700、文件 0600；元数据包含认证秘密，不是公开节点清单。公网 IP 探测失败时手动输入，不回退到本机私网地址。端口冲突按 TCP/UDP 区分；TCP 与 UDP 443 可以共存。

## 官方规范核对（2026-10-14）

- [Xray REALITY](https://xtls.github.io/config/transports/reality.html)：服务端 target（dest 为旧别名）、serverNames、privateKey、shortIds；Vision 客户端 flow `xtls-rprx-vision`。SNI 必须被目标 TLS 站点接受，探测 TLS 1.3 和 h2，仅通过配置检查不能证明 Reality 可连接。
- [sing-box AnyTLS](https://sing-box.sagernet.org/configuration/inbound/anytls/)：1.12.0 起支持，users[].password + TLS。
- [sing-box HY2](https://sing-box.sagernet.org/configuration/inbound/hysteria2/)：UDP/QUIC，users[].password、TLS、可选 salamander，带宽提示不代表强制链路限速。
- [sing-box TLS](https://sing-box.sagernet.org/configuration/shared/tls/)：本地 certificate_path / key_path。新版 inline ACME 已废弃，使用系统 certbot 单独申请，避免依赖被移除的字段。
- [AnyTLS URI](https://github.com/anytls/anytls-go/blob/main/docs/uri_scheme.md)、[HY2 URI](https://v2.hysteria.network/docs/developers/URI-Scheme/)：密码放入百分号编码 auth，IPv6 加方括号，SNI / 混淆放 query，名称放 fragment；不把带宽写入 URI。

已阅读需求指定的 JollyRoger 380 / 457 / 210 教程用于流程参考，未复制其脚本。361 是 XHTTP，本阶段排除。官方发布 API 本次返回 Xray v26.3.27、sing-box v1.14.3；安装时动态读取稳定版并校验，不假设网页示例与所有历史版本通用。

## 小内存原则

内核使用官方预编译文件，不在 VPS 上编译。下载流式写盘、仅提取所需二进制、不下载 geodata。每节点独立进程利于隔离但增加内存；Go 设置 GOMEMLIMIT=64MiB、GOGC=50、GOMAXPROCS=1，适应一核机器。内存设置是软目标，绝不声称是硬内存上限。256MiB 机器需要实测三个内核进程的合计 RSS 和 QUIC 活跃连接占用，不自动修改 swap/sysctl/IPv6。
