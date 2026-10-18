# v0.4.0 客户端兼容性与手动验收

2026-10-18。当前环境可执行本地 Linux 内核检查，不能直接操作 Windows/iOS GUI；本轮也不读取实际节点凭据或访问 VPS。因此下面所有 GUI 项均为 **NOT TESTED**，没有把协议声明或解析成功写成 GUI 实连。

## 证据矩阵

| 客户端 | Reality + Vision | AnyTLS | HY2 + salamander | 证据及限制 |
|---|---|---|---|---|
| sing-box 1.14.3 命令行 | 公网通过 | 公网通过 | 公网通过 | 既有 RC4 实机 URI→客户端→数据摘要和 HTTPS；不是 GUI |
| Xray 26.3.27 命令行 | 公网通过 | 本项目不使用 | 本项目不使用 | 既有 RC4 实机交叉验证 Reality |
| Mihomo 1.19.32 命令行 | 原生解析通过 | 原生解析通过 | 原生解析通过 | 官方 amd64-v1，SHA/ELF/版本校验；没有 Mihomo 公网连接证据 |
| Clash Verge Rev GUI | NOT TESTED | NOT TESTED | NOT TESTED | 未记录实际 App/内置内核版本；用 NodePilot Mihomo 导出项手动合入本地配置 |
| v2rayN GUI | NOT TESTED | NOT TESTED | NOT TESTED | 官方订阅说明列出三协议；URI 导入后的内核/字段和实连仍需逐项核对 |
| Egern iOS | NOT TESTED | NOT TESTED | NOT TESTED | 官方代理文档列出三协议及 Vision/salamander；无 iOS 设备操作证据 |

上游资料仅用于字段交叉检查（2026-10-18 阅读）：[Mihomo VLESS](https://wiki.metacubex.one/config/proxies/vless/)、[AnyTLS](https://wiki.metacubex.one/config/proxies/anytls/)、[HY2](https://wiki.metacubex.one/config/proxies/hysteria2/)、[v2rayN 订阅说明](https://github.com/2dust/v2rayN/wiki/Description-of-subscription)、[Egern 代理](https://doc.egernapp.com/zh-CN/docs/configuration/proxies/)。这些是上游能力声明，不能替代特定版本测试。旧版 Clash 或缺少对应内核的 GUI 可能不支持 AnyTLS，导入拒绝时记录“不支持/该版本失败”，不要改成其他协议或关闭 TLS。

## 准备和字段核对

1. 在另行授权的测试窗口，由用户在现有 NodePilot 菜单7查看分享链接，菜单10导出私有 Mihomo YAML。文件/剪贴板包含认证信息，只保存在用户本地，不发公开 issue。使用有效公网 CA 证书，普通导入不需要服务端 TLS/Reality 私钥或 Cloudflare Token。
2. 记录日期、OS、App版本、实际内核版本和协议。分别导入一个节点，每次只选正在验收的节点；先关闭自动选择/故障转移，避免流量走另一节点。
3. 核对服务器地址、端口和 SNI。Reality 还需 UUID、TCP、`xtls-rprx-vision`、公钥、shortId、指纹；客户端不填写 target/dest 或服务端私钥。AnyTLS 核对解码后的密码、SNI、TLS启用；HY2 核对认证密码、SNI、UDP、`salamander` 及独立 obfs 密码。不要把 URI 百分号编码后的密码当明文再填一次。
4. 保持证书校验开启：Mihomo `skip-cert-verify: false`，v2rayN 不允许不安全连接，Egern `skip_tls_verify: false`。测试自签名需在客户端明确安装相应公开证书信任，URI 本身不携带信任；不为通过验收改成 insecure。

## 各 GUI 操作

- **Clash Verge Rev**：菜单10导出的内容仅是 `proxies`，不是完整订阅或完整配置。新建本地配置/导入用户自有完整配置，将三个代理项合入 `proxies`，加入一个手选代理组，并使测试流量规则指向该组；开启系统代理，逐节点选择后访问 HTTPS。核对 App 实际使用的 Mihomo 版本。不要直接覆盖原有规则/DNS配置，也不要把 `-t` 成功当成连接通过。
- **v2rayN**：复制一条 URI，使用“从剪贴板导入”对应功能，打开节点编辑器核对字段并选用支持该协议的内核；AnyTLS/HY2 可选择支持它们的 sing-box，Reality 用支持 Vision 的 Xray。启用系统代理并选中该节点。若导入丢字段，先记录 App/内核版本和脱敏字段差异；手工补字段只算“手动配置成功”，不能算“URI 原样导入成功”。具体菜单名随版本可能变化。
- **Egern**：在 iOS 中使用当前版本的添加/导入代理功能，逐条导入 URI并打开详情核对。若该版本没有此 URI 导入入口，可按官方字段手工创建并单独标注。选中该节点、启用 VPN，在 Safari 访问 HTTPS；HY2 的 `auth` 对应认证密码，`obfs_password` 对应独立混淆密码；Reality 的 `user_id` 对应 UUID。不要将 Mihomo YAML 未经核对当作 Egern 原生格式。

## 每个节点的实际通过标准

1. 经选定节点连续访问 `https://example.com` 等正常 HTTPS 页面，证书校验开启且无 TLS 错误；观察客户端连接记录确实命中该代理。只有延迟数字/导入成功不足以通过。保存脱敏的成功时间、版本和结果。
2. 在用户单独授权修改测试节点后，更改端口/密码（Reality 可改 UUID/shortId），重新导出并导入，确认新值同步且实连成功；旧配置在排除连接复用后不能新建成功连接。SNI 变更必须使用覆盖新名称的有效证书/合格 Reality 目标，不能随意填域名。
3. 使用仅在客户端改错的认证参数作为负例，HY2 再测错误 obfs 密码，AnyTLS 再测证书不覆盖的 SNI。负例必须是客户端正常启动后连接失败；恢复正例再次成功。GUI 不暴露字段或无法排除回落时写明未验证，不能自动算通过。
4. 报告按“原样 URI 导入 / 手工补字段 / 手工配置 / HTTPS 实连 / 修改同步 / 负例”分别记录 PASS、FAIL、NOT TESTED；日志截取前删除地址、密码、UUID、公钥组合、完整 URI 和私人域名。三 App × 三协议逐项执行，不把一个成功推广到全部。

GUI 是本次未验收兼容范围，允许在发布说明中保留；若要公开承诺某 GUI 某版本兼容，必须先完成该版本的上述实连验收。
