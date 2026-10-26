#!/usr/bin/env python3
"""Decode stored observation captures (.gsobs bundles) with gr-satellites.

For each finalized cf32 SigMF recording whose target satellite exists in
gr-satellites, run ``gr_satellites <NORAD>`` over the file and write unique
frames to ``<bundle>/decoded/``:

  grsat_frames.kiss   all unique frames (KISS)
  grsat_frames.json   per-frame hex + which frequency offset decoded it
  grsat_summary.json  run parameters and counts

Recordings are centred on the nominal downlink but NOT Doppler-corrected
(+/- ~3.5 kHz at VHF). The FLL only pulls in a few hundred Hz, so each file
is decoded at several frequency offsets and frames are de-duplicated.

Usage (from backend/):
  ./startdev.sh is not needed; run with the GNU Radio env, e.g.
  /opt/ground-station/gr311/bin/python tools/decodebundles.py [--jobs 6] [--only NAME]
"""

from __future__ import annotations

import argparse
import concurrent.futures as cf
import glob
import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

DEFAULT_OBS = Path(__file__).resolve().parents[1] / "data" / "observations"
FEND, FESC, TFEND, TFESC = 0xC0, 0xDB, 0xDC, 0xDD


def find_gr_satellites() -> str:
    prefix = os.environ.get("GNU_RADIO_PREFIX", "/opt/ground-station/gr311")
    candidate = Path(prefix) / "bin" / "gr_satellites"
    return str(candidate) if candidate.exists() else "gr_satellites"


def read_kiss(path: Path) -> list[bytes]:
    """Split a KISS file into frames (strip the command byte)."""
    if not path.exists():
        return []
    frames, cur, esc = [], bytearray(), False
    for b in path.read_bytes():
        if b == FEND:
            if len(cur) > 1:
                frames.append(bytes(cur[1:]))
            cur = bytearray()
        elif esc:
            cur.append(FEND if b == TFEND else FESC if b == TFESC else b)
            esc = False
        elif b == FESC:
            esc = True
        else:
            cur.append(b)
    return frames


def write_kiss(path: Path, frames: list[bytes]) -> None:
    out = bytearray()
    for f in frames:
        out.append(FEND)
        out.append(0x00)
        for b in f:
            if b == FEND:
                out += bytes([FESC, TFEND])
            elif b == FESC:
                out += bytes([FESC, TFESC])
            else:
                out.append(b)
        out.append(FEND)
    path.write_bytes(bytes(out))


def known_norads(grsat: str) -> set[int]:
    """NORAD ids gr-satellites ships SatYAML for (via its own python)."""
    py = Path(grsat).parent / "python"
    code = (
        "from satellites.satyaml.satyaml import SatYAML;import json;"
        "y=SatYAML();print(json.dumps([y.get_yamldata(f).get('norad') for f in y.yaml_files()]))"
    )
    try:
        out = subprocess.run([str(py), "-c", code], capture_output=True, text=True, timeout=60)
        return {int(n) for n in json.loads(out.stdout.strip().splitlines()[-1]) if n}
    except Exception:
        return set()


def decode_bundle(data_path: Path, grsat: str, offsets: list[int], fll_bw: float) -> dict:
    bundle = data_path.parents[1]
    meta = json.loads(data_path.with_suffix(".sigmf-meta").read_text())["global"]
    fs = float(meta["core:sample_rate"])
    norad = int(meta["gs:target_satellite_norad_id"])
    out_dir = bundle / "decoded"
    out_dir.mkdir(exist_ok=True)

    seen: dict[bytes, int] = {}
    errors = []
    t0 = time.time()
    with tempfile.TemporaryDirectory() as tmp:
        for off in offsets:
            kiss = Path(tmp) / f"off{off}.kiss"
            cmd = [grsat, str(norad), "--rawfile", str(data_path), "--samp_rate", str(fs),
                   "--iq", "--f_offset", str(off), "--fll_bw", str(fll_bw),
                   "--kiss_out", str(kiss), "--hexdump"]
            proc = subprocess.run(cmd, capture_output=True, text=True)
            if proc.returncode != 0:
                errors.append(f"offset {off}: rc={proc.returncode} {proc.stderr.strip()[-200:]}")
                continue
            for frame in read_kiss(kiss):
                seen.setdefault(frame, off)

    frames = list(seen)
    write_kiss(out_dir / "grsat_frames.kiss", frames)
    (out_dir / "grsat_frames.json").write_text(json.dumps(
        [{"hex": f.hex(), "length": len(f), "f_offset_hz": seen[f]} for f in frames], indent=2))
    summary = {
        "norad_id": norad,
        "sample_rate": fs,
        "offsets_hz": offsets,
        "fll_bw_hz": fll_bw,
        "frames": len(frames),
        "errors": errors,
        "seconds": round(time.time() - t0, 1),
        "decoder": "gr_satellites",
    }
    (out_dir / "grsat_summary.json").write_text(json.dumps(summary, indent=2))
    return {"bundle": bundle.name, **summary}


def main() -> int:
    ap = argparse.ArgumentParser(description="Decode stored .gsobs captures with gr-satellites")
    ap.add_argument("--observations", type=Path, default=DEFAULT_OBS)
    ap.add_argument("--jobs", type=int, default=6)
    ap.add_argument("--only", default="", help="substring filter on bundle name")
    ap.add_argument("--offsets", default="-3000,-2000,-1000,0,1000,2000,3000",
                    help="comma-separated frequency offsets (Hz) to try")
    ap.add_argument("--fll_bw", type=float, default=25.0)
    args = ap.parse_args()

    grsat = find_gr_satellites()
    norads = known_norads(grsat)
    offsets = [int(x) for x in args.offsets.split(",")]

    todo, skipped = [], []
    for data in sorted(glob.glob(str(args.observations / "*.gsobs/recordings/*.sigmf-data"))):
        p = Path(data)
        if args.only and args.only not in str(p):
            continue
        g = json.loads(p.with_suffix(".sigmf-meta").read_text())["global"]
        name = p.parents[1].name
        if not g.get("core:sample_rate") or g.get("core:datatype") != "cf32_le":
            skipped.append((name, "no sample rate / not cf32"))
            continue
        norad = g.get("gs:target_satellite_norad_id")
        if not norad or (norads and int(norad) not in norads):
            skipped.append((name, f"NORAD {norad} not in gr-satellites"))
            continue
        todo.append(p)

    for name, why in skipped:
        print(f"SKIP {name:45s} {why}", flush=True)
    total = 0
    with cf.ProcessPoolExecutor(max_workers=args.jobs) as ex:
        futs = {ex.submit(decode_bundle, p, grsat, offsets, args.fll_bw): p for p in todo}
        for fut in cf.as_completed(futs):
            try:
                r = fut.result()
            except Exception as e:  # keep going on one bad file
                print(f"FAIL {futs[fut].parents[1].name}: {e}", flush=True)
                continue
            total += r["frames"]
            err = f"  errors={len(r['errors'])}" if r["errors"] else ""
            print(f"DONE {r['bundle']:45s} frames={r['frames']:4d}  {r['seconds']:6.1f}s{err}", flush=True)
    print(f"TOTAL frames={total} bundles={len(todo)} skipped={len(skipped)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
