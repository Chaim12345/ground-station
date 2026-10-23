import json
from pathlib import Path

import pytest
from PIL import Image

from handlers.entities.filebrowser import (
    build_observation_bundle_item,
    build_observation_bundle_recordings,
)


def _write_png(path: Path, size=(1200, 700), color=(12, 34, 56)):
    path.parent.mkdir(parents=True, exist_ok=True)
    with Image.new("RGB", size, color=color) as image:
        image.save(path, format="PNG")


def _write_capture(recordings_dir: Path, base_name: str, with_waterfall=True):
    """Create the on-disk file set an automated IQ capture produces."""
    recordings_dir.mkdir(parents=True, exist_ok=True)
    (recordings_dir / f"{base_name}.sigmf-data").write_bytes(b"iq" * 32)
    (recordings_dir / f"{base_name}.sigmf-meta").write_text(
        json.dumps(
            {
                "global": {
                    "core:datatype": "cf32_le",
                    "core:sample_rate": 2400000,
                    "core:version": "1.0.0",
                    "gs:start_time": "2026-08-16T12:12:42+00:00",
                    "gs:finalized_time": "2026-08-16T12:22:42+00:00",
                    "gs:target_satellite_name": "239ALFEROV RS61S",
                },
                "captures": [{"core:frequency": 437025000, "core:sample_start": 0}],
            }
        )
    )
    if with_waterfall:
        _write_png(recordings_dir / f"{base_name}.png", size=(1600, 4000))
        _write_png(recordings_dir / f"{base_name}_waterfall_thumb.png", size=(512, 256))


@pytest.mark.unit
def test_bundle_recording_groups_every_capture_file(tmp_path):
    bundle = tmp_path / "ALFEROV_20260816_121242.gsobs"
    _write_capture(bundle / "recordings", "capture_iq_1")

    recordings, owner_by_path = build_observation_bundle_recordings(bundle)

    assert len(recordings) == 1
    recording = recordings[0]
    assert recording["type"] == "recording"
    assert recording["name"] == "capture_iq_1"
    assert recording["observation_bundle"] == bundle.name
    assert recording["data_file"] == "capture_iq_1.sigmf-data"
    assert recording["meta_file"] == "capture_iq_1.sigmf-meta"
    assert recording["metadata"]["sample_rate"] == 2400000
    assert recording["metadata"]["center_frequency"] == 437025000
    assert recording["download_urls"] == {
        "data": f"/observations/{bundle.name}/recordings/capture_iq_1.sigmf-data",
        "meta": f"/observations/{bundle.name}/recordings/capture_iq_1.sigmf-meta",
    }
    snapshot_file = bundle / "recordings" / "capture_iq_1.png"
    assert recording["snapshot"]["url"] == (
        f"/observations/{bundle.name}/recordings/{snapshot_file.name}"
        f"?v={snapshot_file.stat().st_mtime_ns}"
    )
    assert recording["snapshot"]["thumbnail_url"].startswith(
        f"/observations/{bundle.name}/recordings/capture_iq_1_waterfall_thumb.png?v="
    )

    # Data, metadata, waterfall and waterfall thumbnail all belong to the card.
    assert set(owner_by_path) == {
        "recordings/capture_iq_1.sigmf-data",
        "recordings/capture_iq_1.sigmf-meta",
        "recordings/capture_iq_1.png",
        "recordings/capture_iq_1_waterfall_thumb.png",
    }
    assert set(owner_by_path.values()) == {"capture_iq_1"}


@pytest.mark.unit
def test_bundle_recording_ignores_metadata_without_data_file(tmp_path):
    bundle = tmp_path / "ALFEROV_20260816_121243.gsobs"
    recordings_dir = bundle / "recordings"
    recordings_dir.mkdir(parents=True)
    (recordings_dir / "aborted.sigmf-meta").write_text("{}")

    recordings, owner_by_path = build_observation_bundle_recordings(bundle)

    assert recordings == []
    assert owner_by_path == {}


@pytest.mark.unit
def test_observation_bundle_item_exposes_one_recording_per_capture(tmp_path):
    bundle = tmp_path / "ALFEROV_20260816_121244.gsobs"
    _write_capture(bundle / "recordings", "capture_iq_1")
    (bundle / "decoded" / "telemetry.bin").parent.mkdir(parents=True, exist_ok=True)
    (bundle / "decoded" / "telemetry.bin").write_bytes(b"frames")
    # Cached viewer thumbnails must never surface as observation artifacts.
    _write_png(bundle / "recordings" / "thumbnails" / "capture_iq_1.jpg")

    item = build_observation_bundle_item(bundle)

    assert item["recording_count"] == 1
    assert [recording["name"] for recording in item["recordings"]] == ["capture_iq_1"]

    artifact_paths = {artifact["path"] for artifact in item["artifacts"]}
    assert "recordings/thumbnails/capture_iq_1.jpg" not in artifact_paths

    grouped_paths = {
        artifact["path"] for artifact in item["artifacts"] if artifact["recording_name"]
    }
    assert grouped_paths == {
        "recordings/capture_iq_1.sigmf-data",
        "recordings/capture_iq_1.sigmf-meta",
        "recordings/capture_iq_1.png",
        "recordings/capture_iq_1_waterfall_thumb.png",
    }

    # Only files outside a capture stay visible as standalone artifacts.
    ungrouped_paths = {
        artifact["path"] for artifact in item["artifacts"] if not artifact["recording_name"]
    }
    assert ungrouped_paths == {"decoded/telemetry.bin"}


@pytest.mark.unit
def test_observation_bundle_recording_without_waterfall_has_no_snapshot(tmp_path):
    bundle = tmp_path / "ALFEROV_20260816_121245.gsobs"
    _write_capture(bundle / "recordings", "capture_iq_1", with_waterfall=False)

    item = build_observation_bundle_item(bundle)

    assert item["recording_count"] == 1
    assert item["recordings"][0]["snapshot"] is None
    assert item["thumbnail_url"] is None
