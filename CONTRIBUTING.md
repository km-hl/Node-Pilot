# 上传与隐私检查

每次 push、创建 PR 或上传 Release 前都必须检查精确候选清单，不使用 `git add .`。

1. 只允许管理器源码、公开测试、工具、工作流、许可和中文文档。
2. 不上传 AGENTS.md、会话信息、SSH 主机记录、密码、私钥、节点元数据/分享链接、ACME 账户、运行时备份、内核二进制、日志或本地测试目录。
3. 暂存指定文件后执行 `python3 tools/privacy_audit.py --staged`，工具扫描实际 Git 暂存 blob，不会用已被清理的工作区副本替代。提交后执行 `python3 tools/privacy_audit.py` 扫描 HEAD。配合 `git diff --cached --stat`、`git diff --cached --check` 和实际 diff 人工核对。必要时使用 `--private-input` 隐式输入本次凭据/私有地址进行精确扫描，工具不打印匹配值。
4. 打包只使用 `tools/package.py` 的允许清单；上传前查看 tar 条目和安装器绑定 URL / SHA-256。Release 只上传经检查的包、校验文件和安装器，不上传测试输出。
5. 扫描没有发现内容不等于不存在秘密；不要跳过人工审查。公开报告只保留经过脱敏的环境、版本与验证结果。提交须核对作者/提交者邮箱；自动化提交使用 GitHub noreply，避免上传个人邮箱。

仓库绑定为 `https://github.com/km-hl/Node-Pilot`。运行时根密码和节点秘密仅存于受保护的本机/测试机，不能成为仓库配置。
