"""tasks/telemetrydecoder: decode then delete IQ only after a validated decode."""

import json
from pathlib import Path

import numpy as np
import pytest

from tasks import telemetrydecoder as td


def _bundle(tmp_path: Path, norad: int = 39444) -> Path:
    rec = tmp_path / "SAT.gsobs" / "recordings"
    rec.mkdir(parents=True)
    data = rec / "SAT_20260101_000000.sigmf-data"
    np.zeros(4096, dtype=np.complex64).tofile(data)
    data.with_suffix(".sigmf-meta").write_text(
        json.dumps(
            {
                "global": {
                    "core:datatype": "cf32_le",
                    "core:sample_rate": 48000.0,
                    "gs:target_satellite_norad_id": str(norad),
                },
                "captures": [{"core:sample_start": 0, "core:frequency": 145935000}],
            }
        )
    )
    return data


@pytest.fixture
def fake_grsat(monkeypatch):
    frames: list = []
    monkeypatch.setattr(td, "gr_satellites_binary", lambda: "/fake/gr_satellites")
    monkeypatch.setattr(td, "gr_satellites_knows", lambda norad, grsat: True)
    monkeypatch.setattr(td, "lora_config_for", lambda norad, db=None: None)
    monkeypatch.setattr(
        td,
        "decode_gr_satellites",
        lambda *a, **k: [
            {"hex": f.hex(), "length": len(f), "engine": "gr_satellites", "f_offset_hz": 0}
            for f in frames
        ],
    )
    return frames


def _run(data: Path, delete: bool) -> dict:
    out = data.parents[1] / "decoded" / data.stem
    return td.decode_recording_task(
        str(data), str(out), delete_iq_after=delete, generate_waterfall=False
    )


def test_keeps_iq_when_nothing_decodes(tmp_path, fake_grsat):
    data = _bundle(tmp_path)
    summary = _run(data, delete=True)
    assert summary["trusted_frames"] == 0
    assert summary["iq_deleted_bytes"] == 0
    assert data.exists()


def test_deletes_only_iq_after_validated_frames(tmp_path, fake_grsat):
    fake_grsat.extend([b"\x41" * 256, b"\x42" * 256])
    data = _bundle(tmp_path)
    summary = _run(data, delete=True)
    out = data.parents[1] / "decoded" / data.stem
    assert summary["trusted_frames"] == 2
    assert summary["iq_deleted_bytes"] > 0
    assert not data.exists()
    # Metadata and decoded output survive, so the bundle stays explainable.
    assert data.with_suffix(".sigmf-meta").exists()
    assert td.read_kiss(out / "frames.kiss") == [b"\x41" * 256, b"\x42" * 256]
    assert json.loads((out / "summary.json").read_text())["iq_deleted_bytes"] > 0


def test_keeps_iq_when_delete_not_requested(tmp_path, fake_grsat):
    fake_grsat.append(b"\x01" * 20)
    data = _bundle(tmp_path)
    assert _run(data, delete=False)["trusted_frames"] == 1
    assert data.exists()


def test_lora_crc_failures_do_not_authorise_deletion(tmp_path, monkeypatch):
    monkeypatch.setattr(td, "gr_satellites_binary", lambda: None)
    monkeypatch.setattr(td, "lora_config_for", lambda norad, db=None: {"sf": 10})
    monkeypatch.setattr(
        td,
        "decode_lora",
        lambda *a, **k: [{"hex": "00ff", "length": 2, "engine": "gr-lora_sdr", "crc_valid": False}],
    )
    data = _bundle(tmp_path, norad=60478)
    summary = _run(data, delete=True)
    assert summary["frames"] == 1 and summary["trusted_frames"] == 0
    assert data.exists()


def test_readable_report_lists_every_frame(tmp_path, fake_grsat):
    fake_grsat.extend([b"HELLO SPACE\x00\x01", b"\x41" * 8])
    data = _bundle(tmp_path)
    _run(data, delete=False)
    out = data.parents[1] / "decoded" / data.stem
    report = (out / "decoded.txt").read_text()
    assert "Frames    : 2 (2 validated)" in report
    assert "HELLO SPACE.." in report  # printable view of the payload
    assert b"HELLO SPACE".hex() in report
    frames = json.loads((out / "frames.json").read_text())
    assert all("parsed" in f for f in frames)


def test_kiss_round_trip_escapes_special_bytes(tmp_path):
    frames = [bytes([0xC0, 0xDB, 0x00, 0xDC]), b"plain"]
    path = tmp_path / "f.kiss"
    td.write_kiss(path, frames)
    assert td.read_kiss(path) == frames


def test_unfinalized_recording_is_rejected(tmp_path, fake_grsat):
    data = _bundle(tmp_path)
    meta = json.loads(data.with_suffix(".sigmf-meta").read_text())
    del meta["global"]["core:sample_rate"]
    data.with_suffix(".sigmf-meta").write_text(json.dumps(meta))
    with pytest.raises(ValueError):
        _run(data, delete=True)
    assert data.exists()
