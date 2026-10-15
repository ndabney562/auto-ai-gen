"""Terminate a CLI process group if its coordinator disappears.

Run by CliWorker with a new session. No prompt or credentials are passed in argv.
"""
from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import time


def main() -> int:
    if os.getpgrp() != os.getpid():
        raise RuntimeError("Guard requires a private process group")
    parent = int(sys.argv[1])
    if os.getppid() != parent:
        return 130
    cleanup = json.loads(sys.argv[2])
    child = subprocess.Popen(sys.argv[3:])
    while True:
        if os.getppid() != parent:
            # This helper and the CLI share a private group. Killing it cannot
            # affect the coordinator, other tasks, or an unrelated reused PID.
            try:
                if cleanup:
                    subprocess.run(cleanup, check=False, stdout=subprocess.DEVNULL,
                                   stderr=subprocess.DEVNULL, timeout=15)
            finally:
                os.killpg(os.getpgrp(), signal.SIGKILL)
        result = child.poll()
        if result is not None:
            return result if result >= 0 else 128 - result
        time.sleep(0.05)


if __name__ == "__main__":
    raise SystemExit(main())
