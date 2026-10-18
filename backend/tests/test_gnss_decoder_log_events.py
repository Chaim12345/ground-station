# Copyright (c) 2025 Efstratios Goudelis
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program. If not, see <https://www.gnu.org/licenses/>.

import queue
from types import SimpleNamespace

from demodulators.gnssmonitorudp import parse_gnss_synchro_message
from demodulators.gnsssdrdecoder import DecoderStatus, GNSSSdrDecoder
from pipeline.config.decoderconfigservice import DecoderConfigService
from pipeline.orchestration.gnssfix import derive_gnss_fix_status_from_output


def test_gnss_config_defaults_to_gps_only_but_allows_other_constellations():
    service = DecoderConfigService()
    default = service.get_config("gnss")
    assert default.gnss_enable_gps is True
    assert default.gnss_enable_galileo is False
    assert default.gnss_enable_glonass is False
    assert default.gnss_enable_beidou is False
    assert default.gnss_enable_qzss is False

    selected = service.get_config(
        "gnss", overrides={"gnss_enable_gps": False, "gnss_enable_galileo": True}
    )
    assert selected.gnss_enable_gps is False
    assert selected.gnss_enable_galileo is True


def _build_test_config():
    return SimpleNamespace(
        gnss_sample_rate=4_000_000,
        gnss_total_channels=24,
        gnss_output_rate_ms=500,
        gnss_doppler_max=6000,
        gnss_enable_gps=True,
        gnss_enable_galileo=True,
        gnss_enable_glonass=True,
        gnss_enable_beidou=True,
        gnss_enable_qzss=True,
        baudrate=0,
        framing="gnss",
        config_source="test",
        satellite={},
        transmitter={},
    )


def _build_decoder():
    return GNSSSdrDecoder(
        iq_queue=queue.Queue(),
        data_queue=queue.Queue(),
        session_id="test-session",
        config=_build_test_config(),
        vfo=1,
    )


def test_handle_gnss_log_line_emits_lost_event_channel_first_format():
    decoder = _build_decoder()
    emitted = []
    decoder._send_output_update = lambda payload: emitted.append(payload)
    decoder._send_status_update = lambda *_args, **_kwargs: None

    decoder._handle_gnss_log_line("Loss of lock in channel 3 for satellite GPS PRN 07")

    assert len(emitted) == 1
    assert emitted[0]["event"] == "lost"
    assert emitted[0]["satellite_system"] == "G"
    assert emitted[0]["satellite_prn"] == 7
    assert emitted[0]["channel"] == 3


def test_handle_gnss_log_line_emits_lost_event_satellite_first_format():
    decoder = _build_decoder()
    emitted = []
    decoder._send_output_update = lambda payload: emitted.append(payload)
    decoder._send_status_update = lambda *_args, **_kwargs: None

    decoder._handle_gnss_log_line("Loss of lock for satellite E 29 in channel 2")

    assert len(emitted) == 1
    assert emitted[0]["event"] == "lost"
    assert emitted[0]["satellite_system"] == "E"
    assert emitted[0]["satellite_prn"] == 29
    assert emitted[0]["channel"] == 2


def test_poll_gnss_log_updates_processes_only_loss_lines(tmp_path):
    decoder = _build_decoder()
    log_file = tmp_path / "gnss.log"
    log_file.write_text(
        "\n".join(
            [
                "Tracking in channel 1 for satellite GPS PRN 03",
                "Loss of lock in channel 1 for satellite GPS PRN 03",
                "Successful acquisition in channel 1 for satellite G 3",
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    decoder.gnss_info_log_path = str(log_file)
    decoder.gnss_log_read_offset = 0
    handled_lines = []
    decoder._handle_gnss_log_line = lambda line: handled_lines.append(line.strip())

    decoder._poll_gnss_log_updates()

    assert handled_lines == ["Loss of lock in channel 1 for satellite GPS PRN 03"]


def test_handle_gnss_log_line_increments_loss_of_lock_counter():
    decoder = _build_decoder()
    decoder.stats = {"loss_of_lock_total": 0}
    decoder._send_output_update = lambda _payload: None
    decoder._send_status_update = lambda *_args, **_kwargs: None

    decoder._handle_gnss_log_line("Loss of lock in channel 1 for satellite GPS PRN 03")
    decoder._handle_gnss_log_line("Loss of lock in channel 2 for satellite GPS PRN 11")

    assert decoder.stats["loss_of_lock_total"] == 2


def test_emit_activity_heartbeat_includes_loss_of_lock_fields():
    decoder = _build_decoder()
    decoder.stats = {
        "udp_packets_total": 20,
        "udp_packets_monitor": 4,
        "udp_packets_acquisition": 5,
        "udp_packets_tracking": 9,
        "udp_packets_pvt": 2,
        "udp_monitor_observations": 15,
        "input_write_drops": 0,
        "queue_timeouts": 0,
        "loss_of_lock_total": 7,
        "activity_heartbeats_emitted": 0,
    }
    decoder._last_status = DecoderStatus.TRACKING
    decoder._last_activity_heartbeat_ts = 9.0
    decoder._last_activity_packets_total = 10
    decoder._last_activity_monitor_obs_total = 10
    decoder._last_activity_loss_of_lock_total = 4

    output_payloads = []
    status_payloads = []
    decoder._send_output_update = lambda payload: output_payloads.append(payload)
    decoder._send_status_update = lambda status, info=None: status_payloads.append(
        (status, info or {})
    )

    decoder._emit_activity_heartbeat(now=10.0)

    assert len(output_payloads) == 1
    heartbeat = output_payloads[0]
    assert heartbeat["event"] == "gnss_activity"
    assert heartbeat["loss_of_lock_total"] == 7
    assert heartbeat["loss_of_lock_delta"] == 3

    assert len(status_payloads) == 1
    _, status_info = status_payloads[0]
    assert status_info["gnss_loss_of_lock_total"] == 7
    assert status_info["gnss_loss_of_lock_delta"] == 3


def test_pvt_fix_expires_once_after_valid_updates_stop():
    decoder = _build_decoder()
    decoder.stats.update({"udp_events_emitted": 0, "activity_heartbeats_emitted": 0})
    emitted = []
    decoder._send_output_update = lambda payload: emitted.append(payload)
    decoder._send_status_update = lambda *_args, **_kwargs: None
    decoder._should_emit_udp_event = lambda *_args: True

    decoder._emit_udp_pvt_event({"latitude": 37.9838, "longitude": 23.7275, "solution_status": 5})
    last_pvt_ts = decoder._last_valid_pvt_ts
    assert last_pvt_ts is not None
    assert derive_gnss_fix_status_from_output(emitted[-1]) == "FIX"

    decoder._emit_activity_heartbeat(now=last_pvt_ts + decoder._pvt_fix_timeout_s - 0.1)
    assert not any(event["event"] == "pvt_lost" for event in emitted)

    decoder._emit_activity_heartbeat(now=last_pvt_ts + decoder._pvt_fix_timeout_s + 0.1)
    lost_events = [event for event in emitted if event["event"] == "pvt_lost"]
    assert len(lost_events) == 1
    assert derive_gnss_fix_status_from_output(lost_events[0]) == "NO FIX"
    assert emitted[-1]["event"] == "gnss_activity"
    assert emitted[-1]["has_pvt"] is False

    decoder._emit_activity_heartbeat(now=last_pvt_ts + decoder._pvt_fix_timeout_s + 2.0)
    assert sum(event["event"] == "pvt_lost" for event in emitted) == 1


def test_no_solution_pvt_ends_fix_immediately_without_reusing_coordinates():
    decoder = _build_decoder()
    decoder.stats.update({"udp_events_emitted": 0})
    emitted = []
    decoder._send_output_update = lambda payload: emitted.append(payload)
    decoder._send_status_update = lambda *_args, **_kwargs: None

    decoder._emit_udp_pvt_event({"latitude": 37.9838, "longitude": 23.7275, "solution_status": 5})
    decoder._emit_udp_pvt_event({"latitude": 37.9838, "longitude": 23.7275, "solution_status": 0})
    decoder._emit_udp_pvt_event({"solution_status": 0})
    decoder._emit_udp_pvt_event({"latitude": 37.9838, "longitude": 23.7275, "solution_status": 5})

    assert [event["event"] for event in emitted] == ["nmea_gga", "pvt_lost", "nmea_gga"]
    assert derive_gnss_fix_status_from_output(emitted[1]) == "NO FIX"
    assert derive_gnss_fix_status_from_output(emitted[-1]) == "FIX"
    assert decoder._pvt_fix_active is True


def test_gnss_synchro_parser_reads_valid_pseudorange_flag():
    # GnssSynchro fields 1, 3, and 24: constellation, PRN, valid pseudorange.
    packet = bytes([0x0A, 0x01, ord("G"), 0x18, 0x07, 0xC0, 0x01, 0x01])
    assert parse_gnss_synchro_message(packet) == {
        "system": "G",
        "prn": 7,
        "flag_valid_pseudorange": True,
    }


def test_monitor_events_ignore_invalid_assigned_channels():
    decoder = _build_decoder()
    decoder.stats.update(
        {"udp_monitor_observations": 0, "udp_events_emitted": 0, "udp_events_suppressed": 0}
    )
    emitted = []
    decoder._send_output_update = lambda payload: emitted.append(payload)
    decoder._send_status_update = lambda *_args, **_kwargs: None
    decoder.monitor_receiver = SimpleNamespace(
        poll=lambda: {
            "monitor": [
                {"system": "G", "prn": 7, "flag_valid_word": True},
                {"system": "C", "prn": 9, "flag_valid_pseudorange": True},
            ],
            "acquisition": [
                {"system": "R", "prn": 4, "flag_valid_acquisition": False},
                {"system": "G", "prn": 3, "flag_valid_acquisition": True},
            ],
            "tracking": [
                {"system": "J", "prn": 194, "flag_valid_symbol_output": False},
                {"system": "E", "prn": 11, "flag_valid_symbol_output": True},
            ],
            "pvt": [],
        },
        snapshot_stats=lambda: {},
    )

    decoder._poll_monitor_updates()

    assert [
        (event["event"], event["satellite_system"], event["satellite_prn"]) for event in emitted
    ] == [
        ("tracking", "G", 7),
        ("tracking", "C", 9),
        ("acquisition", "G", 3),
        ("tracking", "E", 11),
    ]
    assert emitted[0]["navigation_verified"] is True
    assert emitted[0]["verification_source"] == "navigation_word"
    assert emitted[1]["navigation_verified"] is True
    assert emitted[1]["verification_source"] == "pseudorange"
    assert emitted[2]["navigation_verified"] is False
    assert emitted[3]["navigation_verified"] is False


def test_navigation_proof_is_not_throttled_by_earlier_tracking_event():
    decoder = _build_decoder()
    decoder.stats.update({"udp_events_emitted": 0, "udp_events_suppressed": 0})
    emitted = []
    decoder._send_output_update = lambda payload: emitted.append(payload)

    candidate = {"system": "G", "prn": 12, "channel_id": 2}
    decoder._emit_udp_satellite_event("tracking", candidate)
    decoder._emit_udp_satellite_event("tracking", {**candidate, "flag_valid_word": True})

    assert [event["navigation_verified"] for event in emitted] == [False, True]
