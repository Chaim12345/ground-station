#!/usr/bin/env python3
"""Decode LoRa from stored observation captures (.gsobs bundles).

Uses the app's own LoRaFlowgraph (gr-lora_sdr) with the satellite's LoRa
settings from the transmitters table. Captures are often recorded below the
flowgraph's minimum rate (2 x LoRa bandwidth), so each file is first resampled
with GNU Radio's rational resampler, then streamed into the flowgraph with
blocking puts (offline, nothing may be dropped).

Writes <bundle>/decoded/lora_frames.json and lora_summary.json.

Usage (from backend/, GNU Radio env on PYTHONPATH/LD_LIBRARY_PATH):
  ./venv/bin/python tools/decodelora.py [--jobs 3] [--only NAME]
"""

from __future__ import annotations

import argparse
import concurrent.futures as cf
import glob
import json
import os
import queue
import sqlite3
import sys
import tempfile
import time
from fractions import Fraction
from pathlib import Path

import numpy as np

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))
DEFAULT_OBS = BACKEND / "data" / "observations"
DEFAULT_DB = BACKEND / "data" / "db" / "gs.db"


def lora_configs(db_path: Path) -> dict[int, dict]:
    """NORAD -> first LoRa transmitter with a stored lora_config."""
    out: dict[int, dict] = {}
    con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    cols = [r[1] for r in con.execute("pragma table_info(transmitters)")]
    for row in con.execute("select * from transmitters where mode = 'LoRa'"):
        d = dict(zip(cols, row))
        cfg = d.get("lora_config")
        if not cfg or cfg == "null" or d.get("norad_cat_id") is None:
            continue
        out.setdefault(int(d["norad_cat_id"]), {"config": json.loads(cfg), "tx": d})
    return out


def resample_to_file(src: Path, fs_in: int, fs_out: int, dst: Path) -> None:
    """Exact rational resampling of a cf32 file with GNU Radio."""
    from gnuradio import blocks, filter, gr

    ratio = Fraction(fs_out, fs_in)
    tb = gr.top_block()
    fsrc = blocks.file_source(gr.sizeof_gr_complex, str(src), False)
    rs = filter.rational_resampler_ccc(interpolation=ratio.numerator, decimation=ratio.denominator)
    fsnk = blocks.file_sink(gr.sizeof_gr_complex, str(dst), False)
    fsnk.set_unbuffered(False)
    tb.connect(fsrc, rs, fsnk)
    tb.run()


def decode_bundle(data_path: Path, entry: dict) -> dict:
    from demodulators.loraconfig import normalize_lora
    from demodulators.loradecoder import LoRaFlowgraph

    bundle = data_path.parents[1]
    meta = json.loads(data_path.with_suffix(".sigmf-meta").read_text())
    fs_in = int(meta["global"]["core:sample_rate"])
    fc = int(meta["captures"][0]["core:frequency"])
    params = normalize_lora(entry["config"])
    fs_out = max(2 * params["bw"], fs_in)
    if fs_out % params["bw"]:
        fs_out = 2 * params["bw"]
    t0 = time.time()

    events: queue.Queue = queue.Queue()  # unbounded: offline, keep every frame
    with tempfile.TemporaryDirectory(dir=os.environ.get("TMPDIR")) as tmp:
        src = data_path
        if fs_out != fs_in:
            src = Path(tmp) / "resampled.cf32"
            resample_to_file(data_path, fs_in, fs_out, src)
        graph = LoRaFlowgraph(fs_out, fc, params, events)
        graph.start()
        try:
            x = np.memmap(src, dtype=np.complex64, mode="r")
            step = 65536
            for i in range(0, len(x), step):
                # Blocking put: never drop samples when the decoder lags.
                graph.source.chunks.put(np.array(x[i : i + step]))
            # Flush the last symbols through the synchronizer.
            graph.source.chunks.put(np.zeros(1 << 16, dtype=np.complex64))
            del x
        finally:
            graph.close(drain=True)

    frames = []
    while not events.empty():
        payload, info = events.get()
        frames.append({
            "hex": payload.hex(),
            "length": len(payload),
            "crc_valid": info.get("crc_valid"),
            "info": {k: (v if isinstance(v, (int, float, bool, str)) else str(v))
                     for k, v in info.items()},
        })
    out_dir = bundle / "decoded"
    out_dir.mkdir(exist_ok=True)
    (out_dir / "lora_frames.json").write_text(json.dumps(frames, indent=2))
    summary = {
        "decoder": "gr-lora_sdr (LoRaFlowgraph)",
        "norad_id": int(meta["global"].get("gs:target_satellite_norad_id", 0)),
        "lora_params": params,
        "input_rate": fs_in,
        "decode_rate": fs_out,
        "frames": len(frames),
        "crc_valid": sum(1 for f in frames if f["crc_valid"]),
        "seconds": round(time.time() - t0, 1),
    }
    (out_dir / "lora_summary.json").write_text(json.dumps(summary, indent=2, default=str))
    return {"bundle": bundle.name, **summary}


def main() -> int:
    ap = argparse.ArgumentParser(description="Decode LoRa from stored .gsobs captures")
    ap.add_argument("--observations", type=Path, default=DEFAULT_OBS)
    ap.add_argument("--db", type=Path, default=DEFAULT_DB)
    ap.add_argument("--jobs", type=int, default=3)
    ap.add_argument("--only", default="")
    args = ap.parse_args()

    configs = lora_configs(args.db)
    todo = []
    for data in sorted(glob.glob(str(args.observations / "*.gsobs/recordings/*.sigmf-data"))):
        p = Path(data)
        if args.only and args.only not in str(p):
            continue
        g = json.loads(p.with_suffix(".sigmf-meta").read_text())["global"]
        norad = g.get("gs:target_satellite_norad_id")
        if not g.get("core:sample_rate") or not norad or int(norad) not in configs:
            continue
        todo.append((p, configs[int(norad)]))

    print(f"LoRa bundles: {len(todo)}", flush=True)
    total = valid = 0
    # Separate processes: GNU Radio flowgraphs + shared memory stay isolated.
    with cf.ProcessPoolExecutor(max_workers=args.jobs) as ex:
        futs = {ex.submit(decode_bundle, p, e): p for p, e in todo}
        for fut in cf.as_completed(futs):
            try:
                r = fut.result()
            except Exception as e:
                print(f"FAIL {futs[fut].parents[1].name}: {e}", flush=True)
                continue
            total += r["frames"]
            valid += r["crc_valid"]
            print(f"DONE {r['bundle']:42s} frames={r['frames']:4d} crc_ok={r['crc_valid']:4d} "
                  f"{r['input_rate']}->{r['decode_rate']} S/s {r['seconds']:6.1f}s", flush=True)
    print(f"TOTAL frames={total} crc_valid={valid} bundles={len(todo)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
