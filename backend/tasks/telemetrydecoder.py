"""
Telemetry decoding of stored IQ recordings (gr-satellites and gr-lora_sdr).

Two engines, picked per satellite:
- ``gr_satellites`` CLI for any satellite with a gr-satellites SatYAML
  (FUNcube/AO-40, AX.25, CCSDS, ...). Recordings are centred on the nominal
  downlink but not Doppler corrected, so the file is decoded at several
  frequency offsets and frames are de-duplicated.
- The app's own ``LoRaFlowgraph`` for satellites whose transmitter has a
  stored ``lora_config``. Captures below 2 x LoRa bandwidth are resampled.

Outputs go to ``<output_dir>``:
  frames.json   unique frames: hex, length, engine, offset/CRC info
  frames.kiss   the same frames as KISS (readable by gr-satellites tooling)
  summary.json  run parameters, counts and the IQ-deletion decision

Deleting the IQ after decoding is only done when frames were decoded (and
for LoRa at least one CRC-valid frame), so a failed or empty decode never
destroys the only copy of a capture.
"""

from __future__ import annotations

import json
import os
import queue
import shutil
import sqlite3
import subprocess
import tempfile
import time
from fractions import Fraction
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

import numpy as np

BACKEND_DIR = Path(__file__).resolve().parents[1]
DEFAULT_DB = BACKEND_DIR / "data" / "db" / "gs.db"
DEFAULT_OFFSETS = (-3000, -2000, -1000, 0, 1000, 2000, 3000)
FEND, FESC, TFEND, TFESC = 0xC0, 0xDB, 0xDC, 0xDD

Log = Callable[[str], None]


def _noop(_: str) -> None:
    return None


# --------------------------------------------------------------------------
# Small helpers
# --------------------------------------------------------------------------


def gr_satellites_binary() -> Optional[str]:
    """Locate gr_satellites, preferring the GNU Radio prefix the app runs with."""
    prefix = os.environ.get("GNU_RADIO_PREFIX")
    if prefix:
        candidate = Path(prefix) / "bin" / "gr_satellites"
        if candidate.exists():
            return str(candidate)
    return shutil.which("gr_satellites")


def read_kiss(path: Path) -> List[bytes]:
    """Split a KISS stream into frames (command byte stripped)."""
    if not path.exists():
        return []
    frames: List[bytes] = []
    cur, esc = bytearray(), False
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


def write_kiss(path: Path, frames: List[bytes]) -> None:
    out = bytearray()
    for frame in frames:
        out += bytes([FEND, 0x00])
        for b in frame:
            if b == FEND:
                out += bytes([FESC, TFEND])
            elif b == FESC:
                out += bytes([FESC, TFESC])
            else:
                out.append(b)
        out.append(FEND)
    path.write_bytes(bytes(out))


def load_sigmf(data_path: Path) -> Dict[str, Any]:
    meta_path = data_path.with_suffix(".sigmf-meta")
    return json.loads(meta_path.read_text())


def lora_config_for(norad_id: int, db_path: Path = DEFAULT_DB) -> Optional[Dict[str, Any]]:
    """Stored LoRa PHY settings for a satellite, read-only."""
    if not db_path.exists():
        return None
    con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        rows = con.execute(
            "select lora_config from transmitters where norad_cat_id = ? and mode = 'LoRa'",
            (norad_id,),
        ).fetchall()
    finally:
        con.close()
    for (cfg,) in rows:
        if cfg and cfg != "null":
            return dict(json.loads(cfg))
    return None


def gr_satellites_knows(norad_id: int, grsat: str) -> bool:
    """True when gr-satellites ships a SatYAML for this NORAD id."""
    python = Path(grsat).parent / "python"
    code = (
        "import sys;from satellites.satyaml.satyaml import SatYAML;"
        f"SatYAML().search_norad({int(norad_id)})"
    )
    try:
        result = subprocess.run(
            [str(python) if python.exists() else "python3", "-c", code],
            capture_output=True,
            timeout=60,
        )
        return result.returncode == 0
    except Exception:
        return False


# --------------------------------------------------------------------------
# Engines
# --------------------------------------------------------------------------


def decode_gr_satellites(
    data_path: Path,
    norad_id: int,
    sample_rate: float,
    offsets=DEFAULT_OFFSETS,
    fll_bw: float = 25.0,
    log: Log = _noop,
    grsat: Optional[str] = None,
) -> List[Dict[str, Any]]:
    grsat = grsat or gr_satellites_binary()
    if not grsat:
        raise RuntimeError("gr_satellites not found (set GNU_RADIO_PREFIX)")
    seen: Dict[bytes, int] = {}
    with tempfile.TemporaryDirectory() as tmp:
        for off in offsets:
            kiss = Path(tmp) / f"off{off}.kiss"
            cmd = [
                grsat, str(norad_id), "--rawfile", str(data_path),
                "--samp_rate", str(sample_rate), "--iq",
                "--f_offset", str(off), "--fll_bw", str(fll_bw),
                "--kiss_out", str(kiss), "--hexdump",
            ]
            proc = subprocess.run(cmd, capture_output=True, text=True)
            if proc.returncode != 0:
                log(f"gr_satellites offset {off:+d} Hz failed: {proc.stderr.strip()[-300:]}")
                continue
            new = 0
            for frame in read_kiss(kiss):
                if frame not in seen:
                    seen[frame] = off
                    new += 1
            log(f"gr_satellites offset {off:+d} Hz: {new} new frame(s)")
    return [
        {"hex": f.hex(), "length": len(f), "engine": "gr_satellites", "f_offset_hz": o}
        for f, o in seen.items()
    ]


def _resample_file(src: Path, fs_in: int, fs_out: int, dst: Path) -> None:
    from gnuradio import blocks, filter, gr

    ratio = Fraction(fs_out, fs_in)
    tb = gr.top_block()
    source = blocks.file_source(gr.sizeof_gr_complex, str(src), False)
    resampler = filter.rational_resampler_ccc(
        interpolation=ratio.numerator, decimation=ratio.denominator
    )
    sink = blocks.file_sink(gr.sizeof_gr_complex, str(dst), False)
    tb.connect(source, resampler, sink)
    tb.run()


def decode_lora(
    data_path: Path,
    center_freq: int,
    sample_rate: float,
    lora_config: Dict[str, Any],
    log: Log = _noop,
) -> List[Dict[str, Any]]:
    from demodulators.loraconfig import normalize_lora
    from demodulators.loradecoder import LoRaFlowgraph

    params = normalize_lora(lora_config)
    fs_in = int(sample_rate)
    fs_out = fs_in if fs_in >= 2 * params["bw"] and fs_in % params["bw"] == 0 else 2 * params["bw"]
    events: queue.Queue = queue.Queue()  # unbounded: offline, keep every frame
    with tempfile.TemporaryDirectory() as tmp:
        src = data_path
        if fs_out != fs_in:
            log(f"Resampling {fs_in} -> {fs_out} S/s for LoRa BW {params['bw']}")
            src = Path(tmp) / "resampled.cf32"
            _resample_file(data_path, fs_in, fs_out, src)
        graph = LoRaFlowgraph(fs_out, int(center_freq), params, events)
        graph.start()
        try:
            samples = np.memmap(src, dtype=np.complex64, mode="r")
            for i in range(0, len(samples), 65536):
                # Blocking put: offline decoding must never drop samples.
                graph.source.chunks.put(np.array(samples[i : i + 65536]))
            graph.source.chunks.put(np.zeros(1 << 16, dtype=np.complex64))
            del samples
        finally:
            graph.close(drain=True)
    frames = []
    while not events.empty():
        payload, info = events.get()
        frames.append(
            {
                "hex": payload.hex(),
                "length": len(payload),
                "engine": "gr-lora_sdr",
                "crc_valid": info.get("crc_valid"),
            }
        )
    log(f"LoRa: {len(frames)} frame(s), {sum(1 for f in frames if f['crc_valid'])} CRC-valid")
    return frames


# --------------------------------------------------------------------------
# Orchestration
# --------------------------------------------------------------------------

# After Doppler removal only the receiver's static offset is left; RTL-SDR
# stations can be several kHz off, so keep the span but use finer steps.
DOPPLER_CORRECTED_OFFSETS = tuple(range(-4000, 4001, 500))


def _doppler_corrected_copy(
    data_path: Path,
    meta: Dict[str, Any],
    norad_id: int,
    sample_rate: float,
    center: int,
    db_path: Path,
    tmp_dir: Path,
    log: Log,
):
    """Doppler-corrected temp copy of the recording, or None if not possible."""
    from tasks import dopplercorrect as dc

    station = dc.resolve_station(meta, data_path, db_path)
    tle = dc.resolve_tle(norad_id, db_path)
    start = dc.capture_start(meta)
    if not (station and tle and start and center):
        missing = [k for k, v in (("station", station), ("tle", tle), ("start", start), ("center", center)) if not v]
        log(f"Doppler correction skipped (missing {', '.join(missing)})")
        return None
    duration = data_path.stat().st_size / 8 / sample_rate
    try:
        t_rel, shift = dc.doppler_track(tle[0], tle[1], station, start, duration, center)
        out = tmp_dir / "doppler_corrected.cf32"
        dc.correct_file(data_path, out, sample_rate, t_rel, shift)
    except Exception as e:  # never block decoding on the optional step
        log(f"Doppler correction failed: {e}")
        return None
    info = {
        "applied": True,
        "station": station,
        "start": start.isoformat(),
        "doppler_hz_range": [round(float(shift.min())), round(float(shift.max()))],
    }
    log(f"Doppler corrected: {info['doppler_hz_range'][1]:+d} .. {info['doppler_hz_range'][0]:+d} Hz")
    return out, info



def decode_recording(
    data_path: Path,
    output_dir: Path,
    db_path: Path = DEFAULT_DB,
    offsets=DEFAULT_OFFSETS,
    log: Log = _noop,
) -> Dict[str, Any]:
    """Decode one SigMF recording; never deletes anything."""
    data_path = Path(data_path)
    meta = load_sigmf(data_path)
    g = meta.get("global", {})
    sample_rate = g.get("core:sample_rate")
    norad = g.get("gs:target_satellite_norad_id")
    captures = meta.get("captures") or []
    if not sample_rate:
        raise ValueError("recording has no core:sample_rate (unfinalized?)")
    if g.get("core:datatype") != "cf32_le":
        raise ValueError(f"unsupported datatype {g.get('core:datatype')}")
    if not norad:
        raise ValueError("recording has no target satellite NORAD id")
    norad_id = int(norad)
    center = int(captures[0]["core:frequency"]) if captures else 0
    t0 = time.time()

    lora_cfg = lora_config_for(norad_id, db_path)
    grsat = gr_satellites_binary()
    frames: List[Dict[str, Any]] = []
    engines: List[str] = []
    doppler_info: Dict[str, Any] = {"applied": False}
    with tempfile.TemporaryDirectory() as tmp:
        decode_path = data_path
        corrected = _doppler_corrected_copy(
            data_path, meta, norad_id, float(sample_rate), center, db_path, Path(tmp), log
        )
        if corrected is not None:
            decode_path, doppler_info = corrected
            # Residual = static receiver offset only: sweep finer steps.
            offsets = DOPPLER_CORRECTED_OFFSETS
        if lora_cfg:
            engines.append("gr-lora_sdr")
            # LoRa demodulation tolerates Doppler; its sync is self-correcting.
            frames += decode_lora(data_path, center, float(sample_rate), lora_cfg, log)
        if grsat and gr_satellites_knows(norad_id, grsat):
            engines.append("gr_satellites")
            frames += decode_gr_satellites(
                decode_path, norad_id, float(sample_rate), offsets, log=log, grsat=grsat
            )
    if not engines:
        log(f"No decoder available for NORAD {norad_id}")

    # A frame is "trusted" if the decoder validated it: gr-satellites only
    # emits frames that passed FEC/CRC; LoRa reports CRC per frame.
    trusted = [f for f in frames if f.get("crc_valid") is not False]
    for frame in frames:
        frame["parsed"] = _parse_frame(frame, norad_id)
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "frames.json").write_text(json.dumps(frames, indent=2, default=str))
    write_kiss(output_dir / "frames.kiss", [bytes.fromhex(f["hex"]) for f in trusted])
    summary = {
        "recording": data_path.name,
        "norad_id": norad_id,
        "sample_rate": sample_rate,
        "center_frequency": center,
        "engines": engines,
        "doppler_correction": doppler_info,
        "frames": len(frames),
        "trusted_frames": len(trusted),
        "seconds": round(time.time() - t0, 1),
        "decoded_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }
    (output_dir / "summary.json").write_text(json.dumps(summary, indent=2))
    write_readable_report(output_dir, summary, frames)
    return summary


def _printable(data: bytes) -> str:
    return "".join(chr(b) if 32 <= b < 127 else "." for b in data)


def _framing_for(norad_id: int, engine: str) -> Optional[str]:
    """gr-satellites framing for the satellite (selects the telemetry parser)."""
    if engine != "gr_satellites":
        return None
    try:
        from satellites.satyaml.satyaml import SatYAML

        from constants import GR_SATELLITES_FRAMING_MAP

        transmitters = SatYAML().search_norad(int(norad_id)).get("transmitters") or {}
        for tx in transmitters.values():
            mapped = GR_SATELLITES_FRAMING_MAP.get(tx.get("framing"))
            if mapped is not None:
                return getattr(mapped, "value", mapped)
    except Exception:
        return None
    return None


def _parse_frame(frame: Dict[str, Any], norad_id: int) -> Dict[str, Any]:
    """Run the app's telemetry parser; never raise."""
    payload = bytes.fromhex(frame["hex"])
    try:
        from telemetry.parser import TelemetryParser

        parser = TelemetryParser()
        if frame.get("engine") == "gr-lora_sdr":
            return parser.parse(payload, protocol_hint="lora", sat_hint=str(norad_id))
        framing = _framing_for(norad_id, frame.get("engine", ""))
        hint = {"framing": framing} if framing else None
        return parser.parse(payload, parser_hint=hint)
    except Exception as e:  # parsing is best-effort; raw frames are kept anyway
        return {"parser": None, "success": False, "error": str(e)}


def write_readable_report(output_dir: Path, summary: Dict[str, Any], frames: List[Dict[str, Any]]) -> Path:
    """decoded.txt: what was received, readable in the file browser."""
    lines = [
        f"Recording : {summary['recording']}",
        f"Satellite : NORAD {summary['norad_id']}",
        f"Engines   : {', '.join(summary['engines']) or 'none'}",
        f"Frames    : {summary['frames']} ({summary['trusted_frames']} validated)",
    ]
    dop = summary.get("doppler_correction") or {}
    if dop.get("applied"):
        lines.append(f"Doppler   : corrected {dop.get('doppler_hz_range')} Hz")
    lines.append("")
    for i, frame in enumerate(frames, 1):
        payload = bytes.fromhex(frame["hex"])
        parsed = frame.get("parsed") or {}
        head = f"--- frame {i}: {len(payload)} bytes, {frame.get('engine')}"
        if frame.get("crc_valid") is not None:
            head += f", CRC {'ok' if frame['crc_valid'] else 'FAILED'}"
        if frame.get("f_offset_hz") is not None:
            head += f", offset {frame['f_offset_hz']:+d} Hz"
        lines.append(head)
        if parsed.get("parser"):
            lines.append(f"parser    : {parsed['parser']}")
        hdr = parsed.get("frame") or {}
        if hdr.get("source") or hdr.get("destination"):
            lines.append(f"AX.25     : {hdr.get('source')} -> {hdr.get('destination')}")
        tel = parsed.get("telemetry")
        printable_ratio = sum(32 <= b < 127 or b in (9, 10, 13) for b in payload) / max(len(payload), 1)
        if printable_ratio >= 0.85:
            # Beacons / CW-style text and APRS messages.
            lines.append(f"text      : {payload.decode('ascii', 'replace').strip()}")
        # The generic fallback is a heuristic hex/float dump, not decoded data.
        if isinstance(tel, dict) and tel and tel.get("format") != "raw":
            fields = {k: v for k, v in tel.items() if k not in ("text", "ascii", "hex", "raw_hex", "analysis")}
            if fields:
                lines.append("telemetry : " + json.dumps(fields, default=str)[:4000])
        lines.append(f"ascii     : {_printable(payload)}")
        lines.append(f"hex       : {payload.hex()}")
        lines.append("")
    path = output_dir / "decoded.txt"
    path.write_text("\n".join(lines))
    return path


def _delete_iq(data_path: Path, log: Log) -> int:
    """Delete only the large .sigmf-data. Metadata, waterfalls and the
    thumbnail stay so the bundle remains browsable and explainable."""
    try:
        size = data_path.stat().st_size
        data_path.unlink()
        log(f"Deleted IQ data {data_path.name} ({size / 1e6:.0f} MB)")
        return size
    except FileNotFoundError:
        return 0


def decode_recording_task(
    recording_path: str,
    output_dir: str,
    delete_iq_after: bool = False,
    generate_waterfall: bool = True,
    _progress_queue=None,
):
    """Background-task entry point (see tasks/registry.py)."""

    def log(message: str) -> None:
        if _progress_queue is not None:
            _progress_queue.put({"type": "output", "output": message, "stream": "stdout"})

    data_path = Path(recording_path)
    if data_path.suffix != ".sigmf-data":
        data_path = Path(f"{recording_path}.sigmf-data")
    if not data_path.exists():
        raise FileNotFoundError(f"Recording not found: {data_path}")

    # Build the waterfall before the IQ can be removed; the executor skips the
    # separate auto-waterfall task for decode-and-delete so they cannot race.
    if generate_waterfall:
        try:
            from tasks.waterfallgenerator import generate_waterfall_task

            generate_waterfall_task(str(data_path.with_suffix("")), _progress_queue=_progress_queue)
        except Exception as e:  # waterfall is a nicety, never block decoding
            log(f"Waterfall generation failed: {e}")

    summary = decode_recording(data_path, Path(output_dir), log=log)
    deleted = 0
    if delete_iq_after:
        if summary["trusted_frames"] > 0:
            deleted = _delete_iq(data_path, log)
        else:
            log("Keeping IQ: no validated frames decoded")
    summary["iq_deleted_bytes"] = deleted
    (Path(output_dir) / "summary.json").write_text(json.dumps(summary, indent=2))
    if _progress_queue is not None:
        _progress_queue.put(
            {
                "type": "output",
                "output": f"Decoded {summary['trusted_frames']} frame(s)",
                "stream": "stdout",
                "progress": 100,
            }
        )
    return summary
