# NodePilot

轻量 VPS 节点搭建与管理工具，首版 `0.1.0`。支持 **VLESS + TCP + REALITY + Vision**、**AnyTLS + TLS**、**Hysteria2 + TLS + QUIC**。Bash 引导安装，Python 标准库终端菜单，Xray / sing-box 官方预编译内核，不运行 Web 服务。

**当前状态：三协议已在 Debian 13 真实 VPS 上通过外网数据传输、HTTPS、生命周期及受限资源短测。** TLS 使用明确标记的测试自签名证书并在客户端导入信任，未关闭验证；ACME 实际签发、完整 256MiB 实机长测及 GUI 客户端矩阵仍待验证。详见 [真实 VPS 测试报告](docs/VPS_TEST_REPORT.md)、[本地记录](docs/VALIDATION.md) 和 [验收清单](docs/VPS_ACCEPTANCE.md)。

仓库：[km-hl/Node-Pilot](https://github.com/km-hl/Node-Pilot)。发布后的一键入口：

```bash
bash <(curl -fsSL https://raw.githubusercontent.com/km-hl/Node-Pilot/main/install.sh)
```

安装器绑定固定版本 Release 附件 URL 和 SHA-256，不从未校验包执行管理器。

## 本地源码安装

将源码放到支持的 VPS，进入项目目录，以 root 运行：

```bash
bash install.sh --local
```

安装完成立即打开菜单。以后只需：

```bash
nodepilot
```

支持 Debian 12/13、Ubuntu 22.04/24.04，amd64/arm64，要求 systemd 正在运行，Python >=3.10。依赖按需通过系统签名软件源安装。通过 SSH 使用时需要 TTY；批量工具可用 `ssh -t`。脚本可从 `/dev/tty` 读取输入，不依赖管道 stdin。

仓库源码安装器与 Release 附件均绑定到本项目公开仓库；未发布对应附件时下载会安全中止。手工修改管理器源码后必须重新打包并生成安装器，不能沿用旧 SHA-256。

## 菜单

菜单支持三种节点创建、列举、逐节点修改/删除/重启、分享链接、日志、内核与脚本升级、备份/恢复、证书更换和网络诊断。创建时才按需安装所选内核；没有预装的内核可在菜单 9 单独安装。

- Reality 默认建议 TCP 443，AnyTLS 建议 TCP 8443，HY2 建议 UDP 443。始终先查占用；TCP 和 UDP 同一端口号可以共存。
- 自动生成 UUID、密码、x25519 和 shortId；可输入自己的参数。Reality 私钥不出现在分享链接中。
- Reality target 自定义，不把某个域名默认认作可用目标。安装/修改时验证每个 serverName 的 TLS 证书、TLS 1.3 与 h2；这只是前置检查，不能证明完整 Reality 联通。鉴权失败可能向目标转发流量，请评估所选站点及回落滥用风险。
- TLS 方式：导入 PEM 完整链和无口令私钥、Let's Encrypt HTTP-01、明确标记的 30 天测试自签名。必须匹配 SNI；不生成 `insecure=1`。
- HTTP-01 必须确认全部 A/AAAA 指向本机及公网 TCP 80 可达；检查本机端口占用，独立 certbot 配置目录，不会停止 Caddy/Nginx。首次申请可安装系统 certbot 包，需要同意 CA 条款。暂不支持 DNS-01。
- 独立 `nodepilot-renew.timer` 每天检查本项目 ACME 证书，进入到期前 30 天时续期；复制到新修订后检查、重启，失败保留旧配置。导入证书/测试证书不自动续期，可在菜单 10 更换。
- HY2 可配置 salamander、认证密码、SNI、UDP 端口和带宽提示。带宽值影响拥塞控制，不能提高实际链路容量，也不等同于硬限速。
- 只读展示 ss、内存与主机防火墙规则。**不会自动放行端口**；请在主机防火墙和云安全组自行放行准确的 TCP/UDP 端口。规则存在也不能证明公网 UDP 可达。
- 删除需要 YES 和再次输入节点 ID；卸载需要 YES 和 UNINSTALL。

## 数据、安全与恢复

```text
/etc/nodepilot/
  .owner / .lock
  nodes/n-<随机ID>/
    current -> revisions/r-<随机修订>
    revisions/r-<随机修订>/node.json / config.json / certificate.pem / key.pem
    pending.json                 仅在事务中存在
  backups/                       私有、含凭据的 JSON 备份
  acme/n-<随机ID>/                独立 certbot 配置/账户/日志
/opt/nodepilot/
  app/<项目包SHA256>/             带安装文件清单的程序修订
  app/current                    原子选择当前程序版本
  cores/<xray或sing-box>/<版本>/   本项目校验内核
/etc/systemd/system/
  nodepilot-n-<随机ID>.service
  nodepilot-renew.service / nodepilot-renew.timer
/usr/local/bin/nodepilot
```

敏感目录 0700、配置与备份 0600。管理器退出后不驻留。分享输出明确提示含认证凭据，日志也可能含敏感信息。每个节点一个独立服务，使用唯一随机 ID；不改写 `/etc/s-box/sb.json`、`/etc/sing-box/config.json` 或机器原有 Xray 配置。

修改先备份，生成独立修订，经 `xray run -test -config` / `sing-box check -c` 校验，原子切换 `current` 使元数据和配置一致。启动后检查服务是否仍 active；失败自动回滚。事务记录落盘，中断后再次进入菜单恢复。多会话和证书定时操作使用同一文件锁。

服务单元内容必须与项目生成内容一致才可停止/卸载；遇到外部修改时安全中止。程序卸载根据文件清单删除，不递归删除未知用户添加的程序文件。私有备份保留在 `/etc/nodepilot/backups/`；不卸载系统依赖，不删除其他软件的证书/服务，不改 IPv6/sysctl/全局网络参数。

恢复需要可信备份和**空节点集合**，逐节点配置检查并启动，失败回退已恢复节点。证书恢复为导入模式；不会误认为备份已迁移 ACME 账户或自动续期能力，需要在菜单重新配置 ACME。历史节点修订、备份和旧内核版本暂不自动清理；频繁更新需要注意磁盘空间。

## 下载与升级

内核只从官方 GitHub 稳定发布获取。API 必须提供附件 SHA-256，下载采用 HTTPS、超时、重试和大小上限，验证压缩包后仅提取所需内核，再核对 ELF 架构和执行报告版本。不提供校验值或校验失败会中止；不回退到执行未校验文件。

可用 `NODEPILOT_MIRROR=https://可信镜像前缀` 更换内核附件传输：官方失败后请求 `前缀/原始HTTPS-URL`，校验值仍来自官方 API。官方元数据不可达时安全中止；不绕过 TLS。SHA-256 + 官方 HTTPS 验证的是发布来源与下载一致性，不声称等价于独立维护者签名。

重复安装不改写节点；源码升级：

```bash
bash install.sh --local --upgrade
```

官方内核升级会先用候选内核检查全部受影响节点，再切换独立内核路径和逐个重启；失败回滚。绑定的安装器记录自己的 GitHub 仓库，菜单 9 从该仓库稳定发布获取 `install.sh` 并校验官方附件 SHA-256 后升级；自定义未绑定源的安装提示使用源码升级命令。

## 客户端

生成标准 `vless://`、`anytls://`、`hysteria2://`，正确编码密码、query 和中文名称，IPv6 加方括号。HY2 链接不携带机器相关的带宽值。自签名必须手动在客户端导入节点 `certificate.pem` 为信任证书，URI 不能单独传递完整信任关系；不会默认关闭证书校验。

已将导出 URI 解析为 sing-box 1.14.3 客户端配置，三协议均完成真实传输；Reality 另通过 Xray 26.3.27 客户端交叉验证。Mihomo/Clash Meta、v2rayN、Egern GUI 导入尚未逐一实测，不能把结果推广成所有客户端兼容；旧版 Clash 和不支持 AnyTLS 的客户端不能使用 AnyTLS。遇到 URI 不支持时手动配置其受支持的出站类型。此版本不导出 Mihomo YAML，不提供订阅服务器。

## 1c / 256MiB

管理层仅使用 Bash 和 Python 标准库，无 pip、数据库、Web 后台或常驻管理进程；流式下载，预编译内核，不在 VPS 编译，也不下载 geodata。服务默认 `GOMAXPROCS=1`、`GOMEMLIMIT=64MiB`、`GOGC=50`。

`GOMEMLIMIT` 是 Go 内存软目标，**不是 RSS 硬上限**。真实 VPS 的单核、192MiB、禁 swap 测试组中，冷启动三内核与测试 Python 进程完成三协议共 24MiB、最多 4 并发传输和公网 HTTPS，进程组峰值约 171.5MiB，无 OOM。实际 VPS 配置为 1c/2GiB；测试组不包含完整 OS、SSH 和 ACME 续期，因此仍不能宣称完整 256MiB 机器长期验收通过。正式服务没有自动设置硬内存限制、swap 或全局 sysctl。

## 开发验证

在 Linux 项目目录：

```bash
python3 -m unittest discover -s tests -v
bash -n install.sh
shellcheck install.sh
python3 nodepilot self-check
python3 tools/native_check.py --download
```

最后一条仅将已校验官方内核保存到忽略的 `.test-tools/`，执行真实配置检查，不部署节点。后续可不带 `--download`。

可选 root/systemd 检查（有真实本机副作用，务必在测试系统运行）：

```bash
sudo python3 tools/service_smoke.py
sudo python3 tools/install_smoke.py
```

服务检查只创建 `/run` 临时服务和 loopback 监听，测试结束删除本次节点与单元；安装检查拒绝所有已有 NodePilot 路径，测试 `/opt`、`/etc`、命令、timer 的安装/升级与 TTY 后清理。它们不验证公网连接。

离线生成（元数据 schema 见 [架构](docs/ARCHITECTURE.md)）：

```bash
python3 nodepilot render /root/node.json --output /root/config.json
python3 nodepilot share /root/node.json --show-secrets
```

GitHub Actions 会执行上传范围/秘密指标检查、单元测试、shellcheck、官方配置校验及可复现打包；以仓库实际运行结果为准。

## 发布一键安装器

发布前先按 [上传隐私检查](CONTRIBUTING.md) 审查源码、提交身份、Git blob 和附件；本仓库示例：

```bash
python3 tools/package.py --repo km-hl/Node-Pilot --tag v0.1.0
python3 tools/release_audit.py --repo km-hl/Node-Pilot --tag v0.1.0
```

打包只生成 `dist/nodepilot-0.1.0.tar.gz`、校验文件和带**固定附件 URL / SHA-256**的 `dist/install.sh`，不自动上传。将审核后的生成安装器同步到源码 `install.sh`，提交后执行 release_audit 对比附件与 Git HEAD。Release 必须包含 `install.sh` 附件才能在线升级。对应版本入口：

```bash
bash <(curl -fsSL https://github.com/km-hl/Node-Pilot/releases/download/v0.1.0/install.sh)
```

修改项目文件后必须重新打包和生成校验值。项目包仅含显式允许的管理器文件，不包含运行时配置、测试内核或凭据。

## 范围与许可

本阶段不实现 XHTTP/CDN、Trojan/VMess/Shadowsocks、Web 面板、多机管理或订阅服务。流程参考需求列出的教程，协议配置以官方文档为准，不复制第三方一键脚本。源码采用 [MIT License](LICENSE)；下载的 Xray/sing-box 使用各自上游许可证。
