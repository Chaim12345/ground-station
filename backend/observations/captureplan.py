"""Capture sample-rate planning for bandwidth-limited (network) receivers.

Public SpyServers deliver only 30-45 % of the samples at ~150-230 kS/s but
~95-100 % at ~40-50 kS/s (measured live), and every lost block is a hole in
the recording. So a network capture should ask for the narrowest rate that
still holds the signal plus its full Doppler excursion, not a fixed default.
"""

from __future__ import annotations

from typing import Any, Dict, Iterable, Optional

# LEO range-rate is at most ~7.6 km/s -> Doppler = f * v / c.
MAX_RANGE_RATE_MPS = 7_600.0
SPEED_OF_LIGHT = 299_792_458.0

# Floor/ceiling for network captures: below 25 kS/s filter skirts and
# frequency errors start to bite; above 256 kS/s public servers drop data.
MIN_NETWORK_RATE = 25_000.0
MAX_NETWORK_RATE = 256_000.0


def max_doppler_hz(frequency_hz: float) -> float:
    return abs(frequency_hz) * MAX_RANGE_RATE_MPS / SPEED_OF_LIGHT


def signal_bandwidth_hz(transmitter: Dict[str, Any]) -> float:
    """Occupied bandwidth estimate for a transmitter record (Hz)."""
    mode = str(transmitter.get("mode") or "").upper()
    baud = float(transmitter.get("baud") or 0)
    lora = transmitter.get("lora_config") or {}
    if isinstance(lora, dict) and lora.get("bw"):
        return float(lora["bw"])
    if mode == "LORA":
        return 125_000.0
    if baud > 0:
        if "PSK" in mode:
            return 2.0 * baud  # BPSK/QPSK main lobe
        if "FSK" in mode or "MSK" in mode or "AFSK" in mode:
            return 2.5 * baud  # Carson-ish for h<=1
        return 2.5 * baud
    if mode in ("FM", "NFM", "FMN"):
        return 16_000.0
    if mode in ("CW", "USB", "LSB", "SSB"):
        return 3_000.0
    if mode in ("APT",):
        return 40_000.0
    return 30_000.0  # unknown: modest default


def required_capture_rate(transmitters: Iterable[Dict[str, Any]]) -> Optional[float]:
    """Complex sample rate needed to hold every transmitter + Doppler.

    Transmitters are assumed to be captured around one centre (the executor
    centres on the midpoint), so the span between their downlinks is added.
    Returns None when no transmitter is known.
    """
    txs = [t for t in transmitters if t and t.get("downlink_low")]
    if not txs:
        return None
    freqs = [float(t["downlink_low"]) for t in txs]
    span = max(freqs) - min(freqs)
    widest = max(signal_bandwidth_hz(t) for t in txs)
    doppler = max(max_doppler_hz(f) for f in freqs)
    # Signal + Doppler both sides + 25 % guard for filter transition bands.
    rate = (span + widest + 2.0 * doppler) * 1.25
    return min(max(rate, MIN_NETWORK_RATE), MAX_NETWORK_RATE)


def plan_network_sample_rate(
    configured_rate: float, transmitters: Iterable[Dict[str, Any]]
) -> float:
    """Rate to request from a network receiver.

    Never above what the user configured, never above what the transmitter
    needs (that only costs dropped blocks), never below MIN_NETWORK_RATE.
    """
    needed = required_capture_rate(transmitters)
    if needed is None:
        return min(float(configured_rate), MAX_NETWORK_RATE)
    return max(min(float(configured_rate), needed), MIN_NETWORK_RATE)
