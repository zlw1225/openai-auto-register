#!/usr/bin/env python3
"""OpenAI 自动注册工具。"""

import argparse
import json
import os
import random
import secrets
import threading
import time
from datetime import datetime

from register import run as register_run

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(SCRIPT_DIR, "config.json")
LOG_PATH = os.path.join(SCRIPT_DIR, "history.log")

_print_lock = threading.Lock()
_log_lock = threading.Lock()
_counter_lock = threading.Lock()


def _tprint(msg: str) -> None:
    with _print_lock:
        print(msg, flush=True)


def load_config() -> dict:
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


def log_result(email: str, success: bool, account_id: str = "") -> None:
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    status = "OK" if success else "FAIL"
    line = f"[{ts}] {status} {email} {account_id}\n"
    with _log_lock:
        with open(LOG_PATH, "a", encoding="utf-8") as f:
            f.write(line)


def generate_random_email(domain: str) -> str:
    return f"{secrets.token_hex(4)}@{domain.strip()}"


def run_once(
    cfg: dict,
    tag: str = "",
    email: str | None = None,
    email_domain: str | None = None,
    password: str | None = "66661adcchat",
    auto_fetch_otp: bool = True,
    inbox_email: str | None = None,
    inbox_url: str | None = None,
) -> bool:
    proxy = cfg.get("proxy")
    if not email and email_domain:
        email = generate_random_email(email_domain)
        _tprint(f"{tag}[*] Generated email: {email}")

    register_cfg = cfg.get("register", {})
    resolved_inbox_email = inbox_email or register_cfg.get("inbox_email")
    resolved_inbox_url = inbox_url or cfg.get("inbox_url") or register_cfg.get("inbox_url")

    token_json = register_run(
        proxy,
        email,
        password,
        auto_fetch_otp,
        resolved_inbox_email,
        resolved_inbox_url,
    )
    if not token_json:
        log_result(email or "unknown", False)
        return False

    token_data = json.loads(token_json)
    final_email = token_data.get("email", "unknown")

    cpa_dir = os.path.expanduser("~/.cli-proxy-api")
    os.makedirs(cpa_dir, exist_ok=True)
    fpath = os.path.join(cpa_dir, f"codex-{final_email}.json")
    with open(fpath, "w", encoding="utf-8") as f:
        f.write(token_json)

    _tprint(f"{tag}[*] Token 已保存到 CPA 目录: {fpath}")
    log_result(final_email, True, token_data.get("account_id", ""))
    return True


def main() -> None:
    parser = argparse.ArgumentParser(
        description="OpenAI 自动注册工具",
        epilog=(
            "Examples:\n"
            "  python run.py --once --email user@example.com\n"
            "  python run.py --once --email-domain thecwf.co.uk\n"
            "  python run.py --count 3 --email-domain thecwf.co.uk\n"
            "  python run.py --once --email-domain thecwf.co.uk --manual-otp\n"
            "  python run.py --once --email user@example.com --inbox-email inbox@example.com\n"
            "  python run.py --once --email user@example.com --inbox-url https://mail.chatgpt.org.uk/inbox@example.com"
        ),
        formatter_class=argparse.RawTextHelpFormatter,
    )
    parser.add_argument("--once", action="store_true", help="只运行一次")
    parser.add_argument("--count", type=int, default=1, help="运行次数，0 表示无限循环")
    parser.add_argument("--parallel", type=int, default=1, help="并行线程数，默认 1")
    parser.add_argument("--email", default=None, help="手动指定注册邮箱")
    parser.add_argument("--email-domain", default=None, help="固定邮箱后缀并自动生成随机前缀")
    parser.add_argument("--inbox-email", default=None, help="手动指定接收验证码的邮箱地址")
    parser.add_argument("--inbox-url", default=None, help="手动指定收件箱直达链接")
    parser.add_argument("--password", default="66661adcchat", help="注册密码")
    parser.add_argument("--manual-otp", action="store_true", help="禁用 Playwright 自动抓取验证码")
    args = parser.parse_args()

    cfg = load_config()
    reg_cfg = cfg.get("register", {})
    sleep_min = reg_cfg.get("sleep_min", 5)
    sleep_max = reg_cfg.get("sleep_max", 30)

    target = 1 if args.once else args.count
    parallel = max(1, args.parallel)
    if parallel != 1:
        print("[Warn] 当前流程存在人工输入兜底，不支持并行，已强制改为单线程。")
        parallel = 1

    print("[Info] OpenAI 自动注册工具")
    print(f"[Info] 代理: {cfg.get('proxy', '无')}")
    print(f"[Info] 目标: {'无限循环' if target == 0 else f'{target} 次'}")
    print(f"[Info] 并行: {parallel} 线程")
    print()

    if parallel == 1:
        total = 0
        success = 0
        while True:
            total += 1
            print(f"\n{'=' * 50}")
            print(f"[{datetime.now().strftime('%H:%M:%S')}] 第 {total} 次（成功 {success}）")
            print(f"{'=' * 50}")

            if run_once(
                cfg,
                email=args.email,
                email_domain=args.email_domain,
                password=args.password,
                auto_fetch_otp=not args.manual_otp,
                inbox_email=args.inbox_email,
                inbox_url=args.inbox_url,
            ):
                success += 1

            if target > 0 and total >= target:
                break

            wait = random.randint(sleep_min, sleep_max)
            print(f"\n[*] 休息 {wait} 秒...")
            time.sleep(wait)

        print(f"\n[Done] 总计 {total} 次，成功 {success} 次")
        return

    counters = {"total": 0, "success": 0, "done": False}

    def worker(worker_id: int) -> None:
        tag = f"[W{worker_id}] "
        while True:
            with _counter_lock:
                if counters["done"]:
                    return
                counters["total"] += 1
                seq = counters["total"]
                if target > 0 and seq > target:
                    counters["total"] -= 1
                    counters["done"] = True
                    return

            _tprint(f"\n{tag}{'=' * 46}")
            _tprint(f"{tag}[{datetime.now().strftime('%H:%M:%S')}] 第 {seq} 次")
            _tprint(f"{tag}{'=' * 46}")

            if run_once(
                cfg,
                tag=tag,
                email=args.email,
                email_domain=args.email_domain,
                password=args.password,
                auto_fetch_otp=not args.manual_otp,
                inbox_email=args.inbox_email,
                inbox_url=args.inbox_url,
            ):
                with _counter_lock:
                    counters["success"] += 1

            with _counter_lock:
                if counters["done"]:
                    return
                if target > 0 and counters["total"] >= target:
                    counters["done"] = True
                    return

            wait = random.randint(sleep_min, sleep_max)
            _tprint(f"{tag}休息 {wait} 秒...")
            time.sleep(wait)

    threads = []
    for i in range(1, parallel + 1):
        t = threading.Thread(target=worker, args=(i,), daemon=True)
        t.start()
        threads.append(t)
        time.sleep(1)

    try:
        for t in threads:
            t.join()
    except KeyboardInterrupt:
        _tprint("\n[!] 收到中断信号，等待当前任务结束...")
        with _counter_lock:
            counters["done"] = True
        for t in threads:
            t.join(timeout=30)

    _tprint(f"\n[Done] 总计 {counters['total']} 次，成功 {counters['success']} 次")


if __name__ == "__main__":
    main()
