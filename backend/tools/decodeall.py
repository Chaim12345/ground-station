#!/usr/bin/env python3
"""Run the app's telemetry decode task over every stored capture.

Same code path as the post-observation pipeline (tasks/telemetrydecoder.py):
decodes with gr-satellites / gr-lora_sdr into <bundle>/decoded/<recording>/
and, with --delete-iq, removes the .sigmf-data only after validated frames.

Usage (from backend/, GNU Radio env on PYTHONPATH/LD_LIBRARY_PATH):
  ./venv/bin/python tools/decodeall.py [--jobs 4] [--delete-iq] [--only NAME]
"""

from __future__ import annotations

import argparse
import concurrent.futures as cf
import glob
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

OBS = Path(__file__).resolve().parents[1] / "data" / "observations"


def run(data: str, delete: bool) -> dict:
    from tasks.telemetrydecoder import decode_recording_task

    p = Path(data)
    out = p.parents[1] / "decoded" / p.stem
    return decode_recording_task(str(p), str(out), delete_iq_after=delete, generate_waterfall=False)


def main() -> int:
    ap = argparse.ArgumentParser(description="Decode every stored capture")
    ap.add_argument("--jobs", type=int, default=4)
    ap.add_argument("--delete-iq", action="store_true")
    ap.add_argument("--only", default="")
    args = ap.parse_args()
    files = [f for f in sorted(glob.glob(str(OBS / "*.gsobs/recordings/*.sigmf-data"))) if args.only in f]
    good = 0
    with cf.ProcessPoolExecutor(max_workers=args.jobs) as ex:
        futs = {ex.submit(run, f, args.delete_iq): f for f in files}
        for fut in cf.as_completed(futs):
            name = Path(futs[fut]).parents[1].name
            try:
                s = fut.result()
            except Exception as e:
                print(f"FAIL {name}: {e}", flush=True)
                continue
            good += s["trusted_frames"] > 0
            print(f"{name:42s} engines={','.join(s['engines']) or '-':26s} frames={s['frames']:3d} "
                  f"valid={s['trusted_frames']:3d} iq_deleted={s['iq_deleted_bytes']/1e6:7.0f}MB "
                  f"{s['seconds']:6.0f}s", flush=True)
    print(f"captures={len(files)} with_valid_frames={good}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
