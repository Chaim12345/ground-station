"""SPUTNIX USP messages carried in UMKA-1 (RS40S) AX.25 information fields.

Layouts follow SPUTNIX's UHF_msgid, PS3_msgid and OBCU_msgid resources,
cross-checked against SatsDecoder's systems/usp.py. See docs/UMKA_TELEMETRY.md
for references, units and capture examples. USP deframers already remove FEC
and the outer encapsulation; there is no AX.25 FCS to remove from this payload.
"""

import math
import struct
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any


@dataclass(frozen=True)
class Field:
    name: str
    offset: int
    fmt: str
    label: str
    unit: str = ""
    divisor: int = 1


UHF_FIELDS = (
    Field("amplifier_temperature", 0, "b", "Amplifier temperature", "°C"),
    Field("radio_temperature", 1, "b", "Radio temperature", "°C"),
    Field("received_signal_strength", 2, "B", "Received signal strength", "dBm", -1),
    Field("idle_signal_strength", 3, "B", "Idle signal strength", "dBm", -1),
    Field("forward_power", 4, "b", "Forward RF power", "dBm"),
    Field("reflected_power", 5, "b", "Reflected RF power", "dBm"),
    Field("reset_count", 6, "B", "Radio reset count"),
    Field("radio_flags", 7, "B", "Radio flags"),
    Field("time_unix_s", 8, "i", "Radio time", "s"),
    Field("uptime", 12, "I", "Radio uptime", "s"),
    Field("receive_bitrate", 16, "I", "Receive bitrate", "bit/s"),
    Field("current", 20, "H", "Radio current", "mA"),
    Field("input_voltage", 22, "h", "Radio input voltage", "V", 1000),
)

POWER_FIELDS = (
    *(
        Field(f"panel_voltage_{i + 1}", i * 2, "H", f"Solar panel {i + 1} voltage", "V", 1000)
        for i in range(3)
    ),
    *(
        Field(f"panel_current_{i + 1}", 6 + i * 2, "H", f"Solar panel {i + 1} current", "mA")
        for i in range(3)
    ),
    Field("battery_current", 12, "h", "Battery current", "mA"),
    *(
        Field(f"channel_current_{i + 1}", 14 + i * 2, "H", f"Output channel {i + 1} current", "mA")
        for i in range(4)
    ),
    *(
        Field(
            f"battery_temperature_{i + 1}",
            22 + i * 2,
            "h",
            f"Battery temperature {i + 1}",
            "°C",
            100,
        )
        for i in range(4)
    ),
    Field("battery_voltage", 34, "h", "Battery voltage", "V", 1000),
    Field("record_id", 36, "I", "Power telemetry sequence"),
    Field("time_unix_s", 40, "i", "Power system time", "s"),
    Field("reset_count", 44, "B", "Power system reset count"),
    Field("reset_reason", 45, "B", "Power system reset reason"),
)

ADCS_FIELDS = (
    Field("time_unix_us", 0, "q", "Attitude-control time", "µs"),
    Field("uptime", 8, "I", "Attitude-control uptime", "s"),
    # Each attitude solution has seven floats followed by a validity/policy byte.
    *(
        Field(f"{prefix}_quaternion_{axis}", start + i * 4, "f", f"{label} quaternion {axis}")
        for prefix, label, start in (
            ("eci", "ECI", 12),
            ("orb", "Orbital", 41),
            ("forced", "Forced ECI", 70),
        )
        for i, axis in enumerate("wxyz")
    ),
    *(
        Field(
            f"{prefix}_angular_velocity_{axis}",
            start + 16 + i * 4,
            "f",
            f"{label} angular velocity {axis}",
            "°/s",
        )
        for prefix, label, start in (
            ("eci", "ECI", 12),
            ("orb", "Orbital", 41),
            ("forced", "Forced ECI", 70),
        )
        for i, axis in enumerate("xyz")
    ),
    Field("latitude", 99, "f", "Latitude", "°"),
    Field("longitude", 103, "f", "Longitude", "°"),
    Field("altitude", 107, "f", "Altitude", "m"),
    Field("task_type", 112, "B", "Attitude-control task type"),
    Field("task_count", 113, "B", "Scheduled attitude-control tasks"),
    *(
        Field(f"wheel_speed_{axis}", 114 + i * 2, "h", f"Reaction wheel {label} speed", "rpm")
        for i, (axis, label) in enumerate(
            (("x_plus", "+X"), ("x_minus", "−X"), ("y_plus", "+Y"), ("y_minus", "−Y"))
        )
    ),
)

LAYOUTS = {
    0x4246: ("UHF radio beacon", 25, UHF_FIELDS),
    0xDE21: ("Power system telemetry", 46, POWER_FIELDS),
    0xF210: ("Attitude-control telemetry", 125, ADCS_FIELDS),
}

NON_TELEMETRY = {
    0x0118: "Command acknowledgement",
    0x0119: "Command rejection",
    0x0C20: "File transfer start",
    0x0C24: "File transfer data",
    0x0C2B: "File transfer size",
    0xDE24: "Power system acknowledgement",
    0xFFF1: "Terminal output",
}


def _add_value(message: dict[str, Any], key: str, value: Any, label: str, unit: str = "") -> None:
    message["values"][key] = value
    message["field_info"][key] = {"label": label, "unit": unit}


def _add_flags(message: dict[str, Any], value: int, flags: tuple[tuple[str, str], ...]) -> None:
    for bit, (key, label) in enumerate(flags):
        _add_value(message, key, bool(value & (1 << bit)), label)


def _decode_message(message: dict[str, Any], body: bytes, fields: tuple[Field, ...]) -> None:
    for field in fields:
        raw = struct.unpack_from("<" + field.fmt, body, field.offset)[0]
        # NaN/Infinity are legal float bit patterns, but not JSON measurements.
        finite = not isinstance(raw, float) or math.isfinite(raw)
        message["raw_fields"][field.name] = raw if finite else str(raw)
        value = (raw if field.divisor == 1 else raw / field.divisor) if finite else None
        _add_value(message, field.name, value, field.label, field.unit)

    timestamp_key = "time_unix_us" if "time_unix_us" in message["values"] else "time_unix_s"
    timestamp = message["values"].pop(timestamp_key)
    divisor = 1_000_000 if timestamp_key == "time_unix_us" else 1
    # Keep the transmitted clock in raw_fields; subsystem clocks may be unsynchronised.
    try:
        utc = datetime.fromtimestamp(timestamp / divisor, timezone.utc).isoformat()
    except (ValueError, OverflowError, OSError):
        utc = None
    label = message["field_info"].pop(timestamp_key)["label"]
    _add_value(message, "time_utc", utc, label + " (UTC)")

    message_id = message["message_id"]
    if message_id == "0x4246":
        message["raw_fields"]["schedule_flags"] = body[24]
        _add_value(message, "active_schedules", body[24] & 0x1F, "Active schedules")
        _add_flags(
            message,
            body[24] >> 5,
            (
                ("reset_during_schedule", "Reset during schedule execution"),
                ("backup_schedule_active", "Backup schedule active"),
            ),
        )
    elif message_id == "0xDE21":
        # The flags occupy four little-endian bytes, not one 32-bit big-endian bitfield.
        flags = int.from_bytes(body[30:34], "little")
        message["raw_fields"]["power_flags"] = flags
        _add_flags(
            message,
            flags,
            (
                ("battery_temperature_low", "Battery temperature below minimum"),
                ("battery_temperature_high", "Battery temperature above maximum"),
                ("heater_1_on", "Battery heater 1 on"),
                ("heater_2_on", "Battery heater 2 on"),
                ("heater_1_manual", "Battery heater 1 manual mode"),
                ("heater_2_manual", "Battery heater 2 manual mode"),
                ("battery_voltage_low", "Battery voltage below minimum"),
                ("battery_voltage_critical", "Battery voltage critical"),
                *(
                    (f"channel_{i}_fault", f"Output channel {i} current limit/fault")
                    for i in range(1, 5)
                ),
                *((f"channel_{i}_on", f"Output channel {i} on") for i in range(1, 5)),
                ("charger_connected", "Charger connected"),
                ("power_switch_fault", "Power switch fault"),
                *((f"aux_channel_{i}_on", f"Auxiliary channel {i} on") for i in range(1, 4)),
            ),
        )
    elif message_id == "0xF210":
        for prefix, label, offset in (
            ("eci", "ECI", 40),
            ("orb", "Orbital", 69),
            ("forced", "Forced ECI", 98),
        ):
            flags = body[offset]
            message["raw_fields"][prefix + "_flags"] = flags
            _add_value(message, prefix + "_policy", flags & 0x7F, label + " solution policy")
            _add_value(
                message, prefix + "_invalid", bool(flags & 0x80), label + " solution invalid"
            )
            if flags & 0x80:
                # An invalid solution is retained in raw_fields, but never presented as a measurement.
                for key in message["values"]:
                    if key.startswith((prefix + "_quaternion_", prefix + "_angular_velocity_")):
                        message["values"][key] = None
        message["raw_fields"]["control_flags"] = body[111]
        message["raw_fields"]["sensor_flags"] = body[122:125].hex()
        _add_value(message, "gnss_invalid", bool(body[111] & 8), "GNSS solution invalid")
        _add_value(message, "ballistics_policy", body[111] & 7, "Orbit solution policy")
        _add_value(message, "reset_reason", body[124] >> 4, "Attitude-control reset reason")
        for bit, axis in enumerate(("x_plus", "x_minus", "y_plus", "y_minus")):
            if body[124] & (1 << bit):
                message["values"]["wheel_speed_" + axis] = None


class USPParser:
    """Decode a sequence of length-delimited USP messages without guessing layouts."""

    def parse(self, payload: bytes) -> dict[str, Any]:
        result: dict[str, Any] = {"format": "usp", "length": len(payload), "messages": []}
        if not payload:
            result["error"] = "Empty USP payload."
            return result
        offset = 0
        while offset < len(payload):
            if len(payload) - offset < 8:
                result["error"] = f"Truncated USP header at payload byte {offset}."
                result["unparsed_hex"] = payload[offset:].hex()
                break
            message_id, source, destination, size = struct.unpack_from("<4H", payload, offset)
            layout = LAYOUTS.get(message_id)
            message: dict[str, Any] = {
                "message_id": f"0x{message_id:04X}",
                "source": source,
                "destination": destination,
                "length": size,
                "name": (
                    layout[0] if layout else NON_TELEMETRY.get(message_id, "Unknown USP message")
                ),
                "status": "unsupported",
                "values": {},
                "field_info": {},
                "raw_fields": {},
            }
            result["messages"].append(message)
            offset += 8
            body = payload[offset : offset + size]
            message["payload_hex"] = body.hex()
            if len(body) != size:
                message["status"] = "malformed"
                message["error"] = (
                    f"Truncated USP message: expected {size} bytes, received {len(body)}."
                )
                break
            # Always advance by the declared size, including unknown messages. This
            # preserves subsequent telemetry and repeated messages in a single frame.
            offset += size
            if layout:
                _, expected_size, fields = layout
                if size != expected_size:
                    message["status"] = "malformed"
                    message["error"] = (
                        f"Unsupported message length: expected {expected_size} bytes, received {size}."
                    )
                    continue
                _decode_message(message, body, fields)
                message["status"] = "decoded"
            elif message_id in NON_TELEMETRY:
                message["status"] = "non_telemetry"
        return result
