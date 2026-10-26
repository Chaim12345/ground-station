"""IQRecorder metadata must make a capture decodable even if stop() never runs."""

import json
import queue
import time

import numpy as np
import pytest

from demodulators.iqrecorder import IQRecorder

pytestmark = pytest.mark.unit


def _iq_message(chunk_id, n=4096, rate=228000.0, freq=145935000):
    return {
        "samples": (np.ones(n) * 0.1).astype(np.complex64),
        "center_freq": freq,
        "sample_rate": rate,
        "timestamp": time.time(),
        "stream_chunk_id": chunk_id,
        "stream_start_sample": chunk_id * n,
        "stream_sample_count": n,
    }


def _wait_for(predicate, timeout=3.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if predicate():
            return True
        time.sleep(0.02)
    return False


def test_sample_rate_persisted_before_stop(tmp_path):
    """A recording killed mid-pass still carries sample rate + capture frequency."""
    q: queue.Queue = queue.Queue()
    path = tmp_path / "rec"
    rec = IQRecorder(q, None, "s1", str(path), target_satellite_norad_id="39444")
    rec.start()
    try:
        q.put(_iq_message(0))
        q.put(_iq_message(1))
        meta_path = tmp_path / "rec.sigmf-meta"
        assert _wait_for(lambda: "core:sample_rate" in json.loads(meta_path.read_text())["global"])

        meta = json.loads(meta_path.read_text())
        assert meta["global"]["core:sample_rate"] == 228000.0
        assert meta["global"]["gs:recording_in_progress"] is True
        assert meta["global"]["gs:target_satellite_norad_id"] == "39444"
        assert meta["captures"][0]["core:frequency"] == 145935000
        assert not (tmp_path / "rec.sigmf-meta.tmp").exists()
    finally:
        rec.stop()


def test_stop_writes_final_metadata(tmp_path):
    q: queue.Queue = queue.Queue()
    station = {"lat": 45.5, "lon": 9.3, "alt": 120.0}
    rec = IQRecorder(q, None, "s1", str(tmp_path / "rec"), station=station)
    rec.start()
    q.put(_iq_message(0))
    assert _wait_for(lambda: rec.total_samples > 0)
    rec.stop()

    meta = json.loads((tmp_path / "rec.sigmf-meta").read_text())
    g = meta["global"]
    assert "gs:recording_in_progress" not in g
    assert g["core:sample_rate"] == 228000.0
    assert "gs:finalized_time" in g
    assert g["gs:recorder_stats"]["samples_written"] == 4096
    assert len(meta["captures"]) == 1
    # Receiver site recorded so offline decoding can predict Doppler.
    assert g["gs:station"] == station
    assert meta["captures"][0]["core:datetime"]


def test_preliminary_metadata_has_no_sample_rate_until_data(tmp_path):
    rec = IQRecorder(queue.Queue(), None, "s1", str(tmp_path / "rec"))
    try:
        g = json.loads((tmp_path / "rec.sigmf-meta").read_text())["global"]
        assert g["gs:recording_in_progress"] is True
        assert "core:sample_rate" not in g
    finally:
        rec.data_file.close()
