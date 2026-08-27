#!/usr/bin/python3
"""Run count-many rounds of 4 experiments each
with configurations (harness on/off) x (guide on/off).

    run_headless_4.py {existing data dir} {count}

SIGINT (^C) schedules the current experiment to be the final one,
which you can recover from with the `--resume` argument.

"""

import argparse
import os
import signal
import subprocess
import sys
import pty

_HERE = os.path.dirname(os.path.abspath(__file__))
LABELS = [f"harness_{h}_guide_{g}" for h in "FT" for g in "FT"]

stop_level = 0
kill_pid = 0

def on_sigint(*args):
    global stop_level
    print("^\\ SIGQUIT to kill now (risky)", file=sys.stderr)
    stop_level = 1

def on_sigquit(*args):
    global stop_level
    global kill_pid
    if kill_pid != 0:
        os.killpg(kill_pid, signal.SIGINT)
        stop_level = 2

def main():
    signal.signal(signal.SIGINT, on_sigint)
    signal.signal(signal.SIGQUIT, on_sigquit)

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("data_dir", help="existing directory to create the "
                        "experiment subdirectory inside")
    parser.add_argument("count", type=int, help="Number of experiments of each type")
    parser.add_argument("--app", required=True,
                        help="app name, forwarded to run_headless.py --app")
    parser.add_argument("--template-path", required=True,
                        help="template directory, forwarded to run_headless.py "
                        "--template-path (proprietary apps: keep it OUTSIDE this repo)")
    parser.add_argument(
        "--resume",
        type=int,
        nargs="?",
        const=0,
        default=0,
        help="Skip the first this-many experiments",
    )
    args = parser.parse_args()

    if not os.path.isdir(args.data_dir):
        parser.error("data_dir is not a directory: {!r} (typo protection)"
                     .format(args.data_dir))
    if args.count <= 0:
        parser.error("Need positive count")

    for i in range(args.count * len(LABELS)):
        label = LABELS[i % len(LABELS)]
        if i >= args.resume:
            print(f"=== EXPERIMENT {i} ===", file=sys.stderr)
            sys.stdout.flush()
            sys.stderr.flush()
            global kill_pid
            process = subprocess.Popen([
                "python3",
                os.path.join(_HERE, "run_headless.py"),
                args.data_dir,
                label,
                "--app", args.app,
                "--template-path", args.template_path,
                "--agent",
            ],
                # This prevents ^C SIGINT from getting delivered to the child process.
                start_new_session=True,
            )
            kill_pid = process.pid
            process.wait()
            kill_pid = 0

            print(f"Exit {process.returncode}")
        if stop_level > 0:
            print(f"Run with --resume {i+1} to pick up from here.", file=sys.stderr)
            return

if __name__ == "__main__":
    main()
