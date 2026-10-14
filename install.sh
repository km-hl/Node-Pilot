#!/usr/bin/env bash
# NodePilot installer. Release packaging embeds the URL and SHA-256 below.
set -Eeuo pipefail
umask 077

PAYLOAD_URL='https://github.com/km-hl/Node-Pilot/releases/download/v0.2.0/nodepilot-0.2.0.tar.gz'
PAYLOAD_SHA256='6e0cea3af11ac189522cc353ccafb2c6adf1dcdbb55dd514059323301e702f6f'
LOCAL=0
UPGRADE=0
for arg in "$@"; do
    case "$arg" in
        --local) LOCAL=1 ;;
        --upgrade) UPGRADE=1 ;;
        --help) printf '%s\n' '用法：bash install.sh [--local] [--upgrade]'; exit 0 ;;
        *) printf '未知参数：%s\n' "$arg" >&2; exit 1 ;;
    esac
done
fail() { printf 'NodePilot 安装失败：%s\n' "$*" >&2; exit 1; }
[[ ${EUID} -eq 0 ]] || fail '请以 root 运行。'
[[ -r /etc/os-release ]] || fail '缺少 /etc/os-release。'
# shellcheck disable=SC1091
source /etc/os-release
case "${ID:-}:${VERSION_ID:-}" in
    debian:12|debian:13|ubuntu:22.04|ubuntu:24.04) ;;
    *) fail '仅支持 Debian 12/13 和 Ubuntu 22.04/24.04。' ;;
esac
case "$(uname -m)" in x86_64|aarch64) ;; *) fail '仅支持 amd64/arm64。' ;; esac
[[ -d /run/systemd/system ]] || fail '需要正在运行的 systemd。'
[[ -r /dev/tty && -w /dev/tty ]] || fail '需要 SSH 交互 TTY（ssh -t）。'
exec 3<>/dev/tty || fail '无法打开 /dev/tty。'

if [[ -e /usr/local/bin/nodepilot && $UPGRADE -eq 0 ]]; then
    [[ -f /opt/nodepilot/.owner && ! -L /opt/nodepilot/.owner && ! -L /opt/nodepilot ]] || fail '现有同名程序不属于 NodePilot。'
    [[ $(cat /opt/nodepilot/.owner) == 'NodePilot schema=1' ]] || fail '所有权标记不匹配。'
    [[ ! -L /usr/local/bin/nodepilot ]] || fail '拒绝同名符号链接。'
    expected_launcher='#!/usr/bin/env bash
# NodePilot owned launcher schema=1
exec python3 /opt/nodepilot/app/current/nodepilot "$@"'
    [[ $(cat /usr/local/bin/nodepilot) == "$expected_launcher" ]] || fail '已有管理命令不属于 NodePilot 或已被修改。'
    printf '%s\n' '已安装，保留配置并打开管理菜单。' >&3
    exec /usr/local/bin/nodepilot menu <&3 >&3 2>&3
fi

packages=()
command -v python3 >/dev/null || packages+=(python3)
if [[ $LOCAL -eq 0 ]]; then
    command -v curl >/dev/null || packages+=(curl)
fi
command -v openssl >/dev/null || packages+=(openssl)
command -v ss >/dev/null || packages+=(iproute2)
command -v sha256sum >/dev/null || packages+=(coreutils)
[[ -s /etc/ssl/certs/ca-certificates.crt ]] || packages+=(ca-certificates)
if [[ ${#packages[@]} -gt 0 ]]; then
    printf '安装缺少的依赖：%s\n' "${packages[*]}" >&3
    apt-get update
    DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends "${packages[@]}"
fi
python3 -c 'import sys; sys.exit(0 if sys.version_info >= (3,10) else 1)' || fail '需要 Python >= 3.10。'
workspace=$(mktemp -d /tmp/nodepilot-install.XXXXXXXX)
trap 'rm -rf -- "$workspace"' EXIT
archive="$workspace/payload.tar.gz"
if [[ $LOCAL -eq 1 ]]; then
    source_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]:-./install.sh}")" && pwd -P)
    [[ -f $source_dir/tools/package.py && -d $source_dir/nodepilot_pkg ]] || fail '--local 需要完整本地项目。'
    python3 "$source_dir/tools/package.py" --archive "$archive"
    PAYLOAD_SHA256=$(sha256sum "$archive" | cut -d ' ' -f 1)
else
    PAYLOAD_URL=${NODEPILOT_SOURCE_URL:-$PAYLOAD_URL}
    PAYLOAD_SHA256=${NODEPILOT_SOURCE_SHA256:-$PAYLOAD_SHA256}
    [[ $PAYLOAD_URL == https://* && $PAYLOAD_SHA256 =~ ^[a-fA-F0-9]{64}$ ]] || fail '源码版尚未绑定发布仓库。使用 --local；发布版安装器包含固定 HTTPS 地址和 SHA-256。'
    curl --proto '=https' --proto-redir '=https' --fail --location --silent --show-error \
        --connect-timeout 15 --max-time 300 --retry 3 --retry-delay 2 \
        --max-filesize 4194304 --output "$archive" "$PAYLOAD_URL" || fail '项目包下载失败。'
fi
printf '%s  %s\n' "$PAYLOAD_SHA256" "$archive" | sha256sum --check --status || fail '项目包 SHA-256 校验失败，拒绝执行。'

# Archive paths are untrusted. Extract only bounded known regular files; no extractall.
python3 - "$archive" "$workspace" <<'PY'
import pathlib, re, sys, tarfile
archive, root = sys.argv[1], pathlib.Path(sys.argv[2]) / 'payload'
root.mkdir(mode=0o700)
with tarfile.open(archive, 'r:gz') as t:
    seen, total = set(), 0
    for m in t:
        if not m.isfile() or not (m.name == 'nodepilot' or re.fullmatch(r'nodepilot_pkg/[a-z_]+\.py', m.name)):
            raise SystemExit('拒绝不安全或未知项目包条目')
        if m.name in seen or m.size > 262144:
            raise SystemExit('重复或过大的项目包条目')
        seen.add(m.name)
        total += m.size
        if total > 4194304:
            raise SystemExit('项目包过大')
        p = root / m.name
        p.parent.mkdir(mode=0o700, exist_ok=True)
        p.write_bytes(t.extractfile(m).read())
        p.chmod(0o600)
    if not {'nodepilot', 'nodepilot_pkg/__init__.py', 'nodepilot_pkg/deploy.py'} <= seen:
        raise SystemExit('不完整项目包')
PY
python3 "$workspace/payload/nodepilot" self-check || fail '管理器自检失败。'
PYTHONPATH="$workspace/payload" python3 -m nodepilot_pkg.deploy "$workspace/payload" "$PAYLOAD_SHA256" "$PAYLOAD_URL" || fail '部署失败，未报告成功。'
printf '%s\n' 'NodePilot 安装完成，立即进入菜单。按协议选择时安装官方校验内核。' >&3
# Clean temporary files before exec: EXIT traps do not run after exec.
rm -rf -- "$workspace"
trap - EXIT
exec /usr/local/bin/nodepilot menu <&3 >&3 2>&3
