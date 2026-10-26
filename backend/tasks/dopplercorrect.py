"""Doppler pre-correction of stored IQ recordings.

LEO Doppler at VHF sweeps ~±3.5 kHz over a pass at up to ~70 Hz/s near
zenith. Narrow-loop demodulators (gr-satellites' FLL/Costas for 1200 bd
BPSK) cannot follow that from a fixed-centre recording, so the recording is
counter-rotated with the predicted Doppler (TLE + station position) before
decoding. What remains is the static receiver/oscillator offset, which the
decoder's offset sweep and FLL handle.
"""

from __future__ import annotations

import json
import re
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

import numpy as np

SPEED_OF_LIGHT_KM_S = 299_792.458
CHUNK_SAMPLES = 1 << 20


def doppler_track(
    tle1: str,
    tle2: str,
    station: Dict[str, float],
    start: datetime,
    duration_s: float,
    center_hz: float,
    step_s: float = 1.0,
) -> Tuple[np.ndarray, np.ndarray]:
    """Return (t_seconds, doppler_hz) from start, sampled every step_s."""
    from skyfield.api import EarthSatellite, load, wgs84

    ts = load.timescale()
    sat = EarthSatellite(tle1, tle2, "sat", ts)
    rx = wgs84.latlon(station["lat"], station["lon"], elevation_m=station.get("alt", 0.0) or 0.0)
    t_rel = np.arange(0.0, duration_s + 2 * step_s, step_s)
    times = ts.from_datetimes([start + timedelta(seconds=float(s)) for s in t_rel])
    topo = (sat - rx).at(times)
    pos = np.asarray(topo.position.km, dtype=float)
    vel = np.asarray(topo.velocity.km_per_s, dtype=float)
    range_rate = np.sum(pos * vel, axis=0) / np.linalg.norm(pos, axis=0)
    doppler = -center_hz * range_rate / SPEED_OF_LIGHT_KM_S
    return t_rel, doppler


def correct_file(
    src: Path,
    dst: Path,
    sample_rate: float,
    t_rel: np.ndarray,
    doppler_hz: np.ndarray,
) -> None:
    """Write src counter-rotated by the Doppler track (continuous phase)."""
    data = np.memmap(src, dtype=np.complex64, mode="r")
    phase = 0.0
    with open(dst, "wb") as out:
        for i in range(0, len(data), CHUNK_SAMPLES):
            block = np.asarray(data[i : i + CHUNK_SAMPLES])
            t = (np.arange(len(block)) + i) / sample_rate
            freq = np.interp(t, t_rel, doppler_hz)
            # phase[n] = phase0 + 2*pi*sum(f)/fs, continuous across chunks
            ph = phase + 2.0 * np.pi * np.cumsum(freq) / sample_rate
            out.write((block * np.exp(-1j * ph).astype(np.complex64)).astype(np.complex64).tobytes())
            phase = float(ph[-1] % (2.0 * np.pi))
    del data


# --------------------------------------------------------------------------
# Context lookup (station position, TLE, capture start)
# --------------------------------------------------------------------------

_COORD_RE = re.compile(r"@\s*(-?\d+(?:\.\d+)?)\s*,\s*(-?\d+(?:\.\d+)?)")


def _station_from_sdr_row(row: Dict[str, Any]) -> Optional[Dict[str, float]]:
    lat, lon = row.get("latitude"), row.get("longitude")
    if lat is None or lon is None:
        match = _COORD_RE.search(str(row.get("name") or ""))
        if not match:
            return None
        lat, lon = float(match.group(1)), float(match.group(2))
    lat, lon = float(lat), float(lon)
    if abs(lat) < 1e-6 and abs(lon) < 1e-6:
        return None  # "0,0" placeholder locations are useless for Doppler
    return {"lat": lat, "lon": lon, "alt": float(row.get("altitude") or 0.0)}


def resolve_station(meta: Dict[str, Any], data_path: Path, db_path: Path) -> Optional[Dict[str, float]]:
    """Station position from SigMF meta, else from the bundle's SDR record."""
    station = meta.get("global", {}).get("gs:station")
    if isinstance(station, dict) and station.get("lat") is not None and station.get("lon") is not None:
        return _station_from_sdr_row({"latitude": station["lat"], "longitude": station["lon"], "altitude": station.get("alt")})

    manifest = data_path.parents[1] / "manifest.json"
    if not manifest.exists():
        return None
    try:
        sessions = json.loads(manifest.read_text())["scheduled_observation"]["sessions"]
        sdr = sessions[0]["sdr"]
    except (KeyError, IndexError, ValueError):
        return None
    row: Dict[str, Any] = {"name": sdr.get("name")}
    if db_path.exists() and sdr.get("id"):
        con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        try:
            cols = [r[1] for r in con.execute("pragma table_info(sdrs)")]
            found = con.execute("select * from sdrs where id = ?", (sdr["id"],)).fetchone()
            if found:
                row = dict(zip(cols, found))
        finally:
            con.close()
    return _station_from_sdr_row(row)


def resolve_tle(norad_id: int, db_path: Path) -> Optional[Tuple[str, str]]:
    if not db_path.exists():
        return None
    con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        row = con.execute("select tle1, tle2 from satellites where norad_id = ?", (norad_id,)).fetchone()
    finally:
        con.close()
    if not row or not row[0] or not row[1]:
        return None
    return row[0], row[1]


def capture_start(meta: Dict[str, Any]) -> Optional[datetime]:
    captures = meta.get("captures") or []
    stamp = captures[0].get("core:datetime") if captures else None
    if not stamp:
        stamp = meta.get("global", {}).get("gs:start_time")
    if not stamp:
        return None
    start = datetime.fromisoformat(str(stamp).replace("Z", "+00:00"))
    return start if start.tzinfo else start.replace(tzinfo=timezone.utc)
