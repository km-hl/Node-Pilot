import argparse
import json
import sys
from pathlib import Path

from . import __version__
from .common import PilotError, atomic_json, read_json
from .config import generate
from .model import validate
from .share import display


def main(argv=None):
    parser = argparse.ArgumentParser(description="NodePilot VPS 节点管理器")
    parser.add_argument("--version", action="version", version=__version__)
    commands = parser.add_subparsers(dest="command")
    commands.add_parser("menu", help="SSH 交互管理菜单（默认）")
    commands.add_parser("self-check", help="离线检查管理器是否能正常导入")
    commands.add_parser("renew", help="续期本项目 ACME 证书（由独立 timer 调用）")
    p = commands.add_parser("targets", help="在本 VPS 实测并筛选非 Cloudflare Reality 目标")
    p.add_argument("--domains", help="逗号分隔的 1–12 个候选域名；默认内置候选")
    p = commands.add_parser("render", help="离线生成服务端配置；不声称内核已验证")
    p.add_argument("metadata", type=Path)
    p.add_argument("--output", type=Path, required=True)
    p = commands.add_parser("share", help="显示含凭据的分享文本")
    p.add_argument("metadata", type=Path)
    p.add_argument("--show-secrets", action="store_true", required=True)
    args = parser.parse_args(argv)
    try:
        if args.command == "render":
            atomic_json(args.output, generate(read_json(args.metadata)))
            print(f"配置已生成：{args.output}；需执行官方内核配置检查。")
        elif args.command == "share":
            print(display(validate(read_json(args.metadata))))
        elif args.command == "self-check":
            from . import manager, menu, installation, project_update, deploy, adoption, importer, releases
            print(f"NodePilot {__version__} 管理器导入检查通过。")
        elif args.command == "targets":
            from .targets import rank_targets
            ranked = rank_targets(args.domains.split(",") if args.domains else None, print)
            if not ranked:
                raise PilotError("没有通过检查的目标；请更换候选域名。")
            for index, item in enumerate(ranked, 1):
                print(f"{index}. {item['sni']}  {item['median_ms']} ms；仍需真实 Reality 客户端验证。")
        elif args.command == "renew":
            from .certificates import due
            from .manager import Manager
            from .system import supported
            supported()
            manager = Manager()
            with manager.locked():
                manager.recover_core()
                manager.recover()
                from .adoption import Adoptions
                Adoptions(manager).recover()
                failures = []
                for n in manager.nodes():
                    if due(n):
                        try:
                            manager.apply(n, probe=False)
                        except PilotError:
                            failures.append(n["id"])
                if failures:
                    raise PilotError("证书续期失败：" + ", ".join(failures) + "；请检查 DNS、TCP 80 和本机日志。")
        else:
            from .menu import Menu
            return Menu().start()
        return 0
    except (PilotError, OSError, ValueError, KeyError, TypeError) as e:
        print(f"NodePilot 失败：{e}", file=sys.stderr)
        return 1
    except (KeyboardInterrupt, EOFError):
        print("已退出。", file=sys.stderr)
        return 130
