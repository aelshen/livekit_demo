"""Pretty-print logs/trace.jsonl — the multi-agent call trace.

Shows every orchestrator delegation and every tool call each expert
sub-agent made, in order, so you can replay a call's reasoning trail without
digging through raw JSON.

Usage:
    python scripts/view_trace.py             # print everything so far
    python scripts/view_trace.py --follow     # keep printing as new lines arrive (like tail -f)
    python scripts/view_trace.py --clear      # delete the trace log and exit
"""

import argparse
import json
import os
import time
from datetime import datetime
from pathlib import Path

TRACE_FILE = Path(__file__).parent.parent / "logs" / "trace.jsonl"


def format_line(line: str) -> str:
    record = json.loads(line)
    ts = datetime.fromtimestamp(record.pop("ts")).strftime("%H:%M:%S")
    event = record.pop("event")
    fields = "  ".join(f"{k}={v}" for k, v in record.items())
    return f"{ts}  {event:<28} {fields}"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--follow", "-f", action="store_true", help="keep printing new lines as they arrive")
    parser.add_argument("--clear", action="store_true", help="delete the trace log and exit")
    args = parser.parse_args()

    if args.clear:
        TRACE_FILE.unlink(missing_ok=True)
        print(f"Cleared {TRACE_FILE}")
        return

    if not TRACE_FILE.exists():
        print(f"No trace log yet at {TRACE_FILE} — run a call first.")
        return

    f = open(TRACE_FILE)
    for line in f:
        print(format_line(line))

    if args.follow:
        print("--- following, ctrl-c to stop ---")
        inode = os.fstat(f.fileno()).st_ino
        while True:
            line = f.readline()
            if line:
                print(format_line(line))
                continue

            # Reopen if the file was recreated/truncated (e.g. another
            # --clear) — otherwise we'd keep reading a dead file forever.
            try:
                current = os.stat(TRACE_FILE)
            except FileNotFoundError:
                current = None

            if current is None or current.st_ino != inode or current.st_size < f.tell():
                f.close()
                if current is None:
                    time.sleep(0.3)
                    continue
                f = open(TRACE_FILE)
                inode = os.fstat(f.fileno()).st_ino
                continue

            time.sleep(0.3)


if __name__ == "__main__":
    main()
