"""Run independent Jev agents in parallel CloakBrowser processes.

Each worker gets its own browser-harness daemon name and CDP port. Environment
variables and credentials from the parent are inherited; run the coordinator
through ``uv run --env-file .env`` as shown below.

    uv run --env-file .env python examples/parallel_processes.py --headless \
      --record-dir artifacts/parallel --keep-open \
      --job https://en.wikipedia.org/wiki/Main_Page \
        'Open the article about Gödel’s incompleteness theorems.' \
      --job https://example.com \
        'Stop when the Example Domain page is visible.'

This example makes real model calls. Every worker is a separate process and
stealth browser, so it uses more memory than multiple tabs in one process.
"""

import argparse
import json
import os
import subprocess
import sys
import threading
from pathlib import Path

KEEP_OPEN_READY = "__JEV_WORKER_READY_TO_CLOSE__"


def run_worker(args):
    # These are set by the coordinator before this process imports jev or
    # browser-harness; both libraries read their routing configuration at import.
    from browser_harness import admin
    from browser_harness.helpers import cdp

    from jev_ultrafast import Agent

    record_dir = Path(args.record_dir) if args.record_dir else None
    try:
        with Agent(args.url, args.goal, record_dir=record_dir) as agent:
            state = agent.snapshot()
            for state in agent.run():
                last = state["history"][-1] if state["history"] else {}
                print(
                    f"{state['elapsed_ms']:>6} ms  {len(state['history']):>2} actions  "
                    f"{state['status']:<7}  {last.get('action', '')}",
                    flush=True,
                )
            if record_dir:
                saved = {**state, "page": {**state["page"]}}
                saved["page"].pop("screenshot", None)
                (record_dir / "state.json").write_text(json.dumps(saved, indent=2))
            print(json.dumps({
                "worker": args.index,
                "status": state["status"],
                "elapsed_ms": state["elapsed_ms"],
                "actions": len(state["history"]),
                "url": state["page"]["url"],
                "record_dir": str(record_dir) if record_dir else None,
            }), flush=True)
            if args.keep_open:
                # Agent tabs normally stay in the background. Activate the completed
                # target so a headed worker displays the page being inspected.
                cdp("Target.activateTarget", targetId=agent.browser.target)
                print(KEEP_OPEN_READY, flush=True)
                sys.stdin.readline()
    finally:
        # A named harness daemon otherwise outlives its worker. The browser is
        # closed separately by Agent/CloakBrowser process shutdown.
        admin.restart_daemon()


def worker_parser():
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--worker", action="store_true")
    parser.add_argument("--index", type=int, required=True)
    parser.add_argument("--url", required=True)
    parser.add_argument("--goal", required=True)
    parser.add_argument("--record-dir")
    parser.add_argument("--keep-open", action="store_true")
    return parser


def coordinator_parser():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--job",
        action="append",
        nargs=2,
        required=True,
        metavar=("URL", "GOAL"),
        help="URL and one natural-language goal; repeat for each independent worker.",
    )
    parser.add_argument("--base-port", type=int, default=9242, help="First worker CDP port (default: 9242).")
    parser.add_argument("--name-prefix", help="Harness daemon prefix (default includes the coordinator PID).")
    parser.add_argument("--headless", action="store_true", help="Set JEV_HEADLESS=1 in every worker.")
    parser.add_argument("--record-dir", help="Save each worker's JPEG frames and final state under this directory.")
    parser.add_argument("--keep-open", action="store_true", help="Keep completed browsers open until Enter is pressed.")
    parser.add_argument("--dry-run", action="store_true", help="Print worker assignments without launching them.")
    return parser


def stream_output(index, stream, destination, ready, stderr=False):
    prefix = f"[worker {index}{' stderr' if stderr else ''}]"
    for raw_line in stream:
        line = raw_line.rstrip("\n")
        if not stderr and line == KEEP_OPEN_READY:
            ready.set()
            print(f"[worker {index}] task complete; browser remains open", file=destination, flush=True)
        else:
            print(f"{prefix} {line}", file=destination, flush=True)
    stream.close()


def release_kept_open_workers(children):
    for child in children:
        if child["ready"].is_set() and child["process"].stdin is not None:
            try:
                child["process"].stdin.write("\n")
                child["process"].stdin.flush()
                child["process"].stdin.close()
            except (BrokenPipeError, OSError):
                pass


def run_coordinator(args):
    if not 1 <= args.base_port <= 65535 or args.base_port + len(args.job) - 1 > 65535:
        raise SystemExit("The requested worker CDP ports must be between 1 and 65535.")

    prefix = args.name_prefix or f"jev-parallel-{os.getpid()}"
    script = str(Path(__file__).resolve())
    record_root = Path(args.record_dir).resolve() if args.record_dir else None
    children = []
    for index, (url, goal) in enumerate(args.job, start=1):
        env = os.environ.copy()
        env.update(
            BU_NAME=f"{prefix}-{index}",
            JEV_BROWSER="stealth",
            JEV_CDP_PORT=str(args.base_port + index - 1),
        )
        if args.headless:
            env["JEV_HEADLESS"] = "1"
        command = [
            sys.executable,
            script,
            "--worker",
            "--index",
            str(index),
            "--url",
            url,
            "--goal",
            goal,
        ]
        worker_record_dir = record_root / f"worker-{index:02d}" if record_root else None
        if worker_record_dir:
            command.extend(["--record-dir", str(worker_record_dir)])
        if args.keep_open:
            command.append("--keep-open")
        label = f"worker {index}: BU_NAME={env['BU_NAME']} JEV_CDP_PORT={env['JEV_CDP_PORT']}"
        if worker_record_dir:
            label += f" RECORD_DIR={worker_record_dir}"
        if args.dry_run:
            print(f"would start {label}")
            continue

        process = subprocess.Popen(
            command,
            env=env,
            text=True,
            bufsize=1,
            stdin=subprocess.PIPE if args.keep_open else subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        ready = threading.Event()
        threads = [
            threading.Thread(target=stream_output, args=(index, process.stdout, sys.stdout, ready), daemon=True),
            threading.Thread(
                target=stream_output,
                args=(index, process.stderr, sys.stderr, ready, True),
                daemon=True,
            ),
        ]
        for thread in threads:
            thread.start()
        children.append({"process": process, "ready": ready, "threads": threads})
        print(f"started {label}", flush=True)

    if args.keep_open and children:
        for child in children:
            while not child["ready"].wait(0.1) and child["process"].poll() is None:
                pass
        kept_open = sum(child["ready"].is_set() for child in children)
        if kept_open:
            try:
                input(f"{kept_open} browser(s) remain open. Press Enter to close them... ")
            except (EOFError, KeyboardInterrupt):
                print()
            finally:
                release_kept_open_workers(children)

    failed = False
    for child in children:
        failed |= child["process"].wait() != 0
        for thread in child["threads"]:
            thread.join(timeout=2)
    if failed:
        raise SystemExit("One or more workers failed.")


def main():
    if "--worker" in sys.argv:
        run_worker(worker_parser().parse_args())
    else:
        run_coordinator(coordinator_parser().parse_args())


if __name__ == "__main__":
    main()
