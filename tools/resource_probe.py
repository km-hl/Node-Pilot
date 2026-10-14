#!/usr/bin/env python3
"""Bounded worker for a dedicated systemd test cgroup; never adjusts global limits."""
import argparse
import hashlib
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from nodepilot_pkg.common import atomic_json, read_json, root_required
from nodepilot_pkg.cores import Cores, verify_elf
from nodepilot_pkg.model import validate
from nodepilot_pkg.system import architecture


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--private-dir", type=Path, required=True)
    p.add_argument("--duration", type=int, default=180)
    p.add_argument("--cold-binaries", action="store_true", help="在测试cgroup中复制并校验新inode，避免预热代码页被计入其他cgroup")
    p.add_argument("--state-dir", type=Path, default=Path("/etc/nodepilot"))
    p.add_argument("--cores-root", type=Path, default=Path("/opt/nodepilot/cores"))
    p.add_argument("--with-caddy", action="store_true", help="同时计量临时测试的共享 Caddy 进程")
    args = p.parse_args()
    root_required()
    if not 10 <= args.duration <= 300:
        p.error("duration must be 10..300 seconds")
    record = read_json(args.private_dir / "test-nodes.json")
    # Parent controller must stop only its recorded test services first.
    cores = Cores(args.cores_root)
    children, handles = [], []
    environment = dict(os.environ, GOMEMLIMIT="64MiB", GOGC="50", GOMAXPROCS="1")
    stats = {"peak_memory_current_bytes": 0, "peak_sum_rss_kib": 0, "peak_swap_current_bytes": 0,
             "samples": 0, "cold_binaries": args.cold_binaries}
    group = next(line.split(":", 2)[2] for line in Path("/proc/self/cgroup").read_text().splitlines() if line.startswith("0::"))
    cgroup = Path("/sys/fs/cgroup") / group.lstrip("/")
    report = args.private_dir / "resource-report.json"
    scratch = tempfile.TemporaryDirectory(prefix="resource-binaries-", dir=args.private_dir) if args.cold_binaries else None
    try:
        binaries = {}
        required = {n["core"] for n in record["nodes"]} | ({"caddy"} if args.with_caddy else set())
        for core in required:
            original = cores.binary(core)
            if scratch:
                copied = Path(scratch.name) / core
                digest = hashlib.sha256()
                with original.open("rb") as src, copied.open("wb") as dst:
                    while True:
                        block = src.read(256 * 1024)
                        if not block:
                            break
                        digest.update(block)
                        dst.write(block)
                check = hashlib.sha256()
                with copied.open("rb") as src:
                    while True:
                        block = src.read(256 * 1024)
                        if not block:
                            break
                        check.update(block)
                if check.digest() != digest.digest():
                    raise RuntimeError("verified core copy integrity mismatch")
                verify_elf(copied, architecture())
                copied.chmod(0o700)
                binaries[core] = copied
            else:
                binaries[core] = original
        for n in record["nodes"]:
            validate(n)
            log = (args.private_dir / (n["protocol"] + "-resource.log")).open("wb")
            os.chmod(log.name, 0o600)
            handles.append(log)
            config = args.state_dir / "nodes" / n["id"] / "current/config.json"
            command = [str(binaries[n["core"]]), "run", "-config" if n["core"] == "xray" else "-c", str(config)]
            children.append(subprocess.Popen(command, stdout=log, stderr=log, env=environment))
        if args.with_caddy:
            log = (args.private_dir / "caddy-resource.log").open("wb")
            os.chmod(log.name, 0o600)
            handles.append(log)
            env = dict(environment, GOMEMLIMIT="32MiB")
            children.append(subprocess.Popen([str(binaries["caddy"]), "run", "--config", str(args.state_dir / "caddy/config.json")],
                                             stdout=log, stderr=log, env=env))
        stats.update(memory_max_bytes=(cgroup / "memory.max").read_text().strip(),
                     swap_max_bytes=(cgroup / "memory.swap.max").read_text().strip(),
                     cpu_max=(cgroup / "cpu.max").read_text().strip(), ready=True)
        end = time.monotonic() + args.duration
        while time.monotonic() < end:
            if any(child.poll() is not None for child in children):
                raise RuntimeError("a test core exited; inspect private logs")
            rss = 0
            for child in children:
                text = Path(f"/proc/{child.pid}/status").read_text()
                rss += int(next(x for x in text.splitlines() if x.startswith("VmRSS:")).split()[1])
            memory = int((cgroup / "memory.current").read_text())
            stats["peak_memory_current_bytes"] = max(stats["peak_memory_current_bytes"], memory)
            stats["peak_swap_current_bytes"] = max(stats["peak_swap_current_bytes"], int((cgroup / "memory.swap.current").read_text()))
            stats["peak_sum_rss_kib"] = max(stats["peak_sum_rss_kib"], rss)
            stats["samples"] += 1
            stats["memory_events"] = (cgroup / "memory.events").read_text().strip()
            atomic_json(report, stats)
            time.sleep(0.5)
        stats["completed"] = True
    except BaseException:
        stats["failed"] = True
        raise
    finally:
        for child in children:
            if child.poll() is None:
                child.terminate()
        for child in children:
            try:
                child.wait(timeout=5)
            except subprocess.TimeoutExpired:
                child.kill()
                child.wait()
        for handle in handles:
            handle.close()
        if scratch:
            scratch.cleanup()
        atomic_json(report, stats)


if __name__ == "__main__":
    main()
