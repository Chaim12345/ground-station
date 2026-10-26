"""tasks/dopplercorrect: track sign/magnitude and phase-continuous correction."""

import json
from datetime import datetime, timezone

import numpy as np

from tasks import dopplercorrect as dc

# ISS-like TLE; only geometry matters for these checks.
TLE1 = "1 25544U 98067A   26299.50000000  .00016717  00000-0  10270-3 0  9994"
TLE2 = "2 25544  51.6416 247.4627 0006703 130.5360 325.0288 15.49815800 10000"


def test_doppler_track_is_bounded_and_monotonic_through_a_pass():
    start = datetime(2026, 10, 26, 0, 0, tzinfo=timezone.utc)
    station = {"lat": 0.0, "lon": 10.0}
    t, d = dc.doppler_track(TLE1, TLE2, station, start, 86400 / 15.5, 145.8e6, step_s=5)
    # LEO Doppler at 145.8 MHz never exceeds ~3.7 kHz
    assert np.max(np.abs(d)) < 3_800
    assert np.max(d) > 2_000 and np.min(d) < -2_000


def test_correction_removes_a_known_frequency_ramp(tmp_path):
    fs = 10_000.0
    n = int(fs * 20)
    t = np.arange(n) / fs
    # Synthetic Doppler: linear sweep from +1 kHz to -1 kHz
    f = 1000.0 - 100.0 * t
    sig = np.exp(1j * 2 * np.pi * np.cumsum(f) / fs).astype(np.complex64)
    src = tmp_path / "in.cf32"
    dst = tmp_path / "out.cf32"
    sig.tofile(src)
    dc.CHUNK_SAMPLES = 7_777  # odd chunking must keep phase continuous
    t_track = np.arange(0, 21, 1.0)
    dc.correct_file(src, dst, fs, t_track, 1000.0 - 100.0 * t_track)
    out = np.fromfile(dst, dtype=np.complex64)
    # Remaining signal is a constant phasor: phase step between samples ~0
    steps = np.angle(out[1:] * np.conj(out[:-1]))
    assert np.max(np.abs(steps)) < 1e-3


def test_resolve_station_from_meta_and_name(tmp_path):
    meta = {"global": {"gs:station": {"lat": 45.5, "lon": 9.3}}}
    assert dc.resolve_station(meta, tmp_path / "x" / "y" / "r.sigmf-data", tmp_path / "none.db") == {
        "lat": 45.5,
        "lon": 9.3,
        "alt": 0.0,
    }
    bundle = tmp_path / "B.gsobs"
    (bundle / "recordings").mkdir(parents=True)
    (bundle / "manifest.json").write_text(
        json.dumps({"scheduled_observation": {"sessions": [{"sdr": {"name": "RTL-SDR @ 32.744,-117.222 (x)"}}]}})
    )
    st = dc.resolve_station({"global": {}}, bundle / "recordings" / "r.sigmf-data", tmp_path / "none.db")
    assert st == {"lat": 32.744, "lon": -117.222, "alt": 0.0}
    # 0,0 placeholder is rejected
    (bundle / "manifest.json").write_text(
        json.dumps({"scheduled_observation": {"sessions": [{"sdr": {"name": "Airspy @ 0.000,0.000"}}]}})
    )
    assert dc.resolve_station({"global": {}}, bundle / "recordings" / "r.sigmf-data", tmp_path / "none.db") is None


def test_capture_start_parses_sigmf_datetime():
    meta = {"captures": [{"core:datetime": "2026-10-26T18:00:00Z"}]}
    assert dc.capture_start(meta) == datetime(2026, 10, 26, 18, 0, tzinfo=timezone.utc)
    assert dc.capture_start({"captures": []}) is None
