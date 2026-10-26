"""observations/captureplan: network receiver sample-rate sizing."""

import pytest

from observations.captureplan import (
    MAX_NETWORK_RATE,
    MIN_NETWORK_RATE,
    max_doppler_hz,
    plan_network_sample_rate,
    required_capture_rate,
    signal_bandwidth_hz,
)

FUNCUBE = {"downlink_low": 145_935_000, "mode": "DBPSK", "baud": 1200}
WREN_LORA = {"downlink_low": 137_925_000, "mode": "LoRa", "lora_config": {"sf": 10, "bw": 125_000}}
GMSK_9K6 = {"downlink_low": 437_500_000, "mode": "GMSK", "baud": 9600}


def test_doppler_scales_with_frequency():
    assert max_doppler_hz(145.9e6) == pytest.approx(3_700, rel=0.05)
    assert max_doppler_hz(437.5e6) == pytest.approx(11_090, rel=0.05)


def test_bandwidth_estimates():
    assert signal_bandwidth_hz(FUNCUBE) == 2_400
    assert signal_bandwidth_hz(WREN_LORA) == 125_000
    assert signal_bandwidth_hz({"mode": "FM"}) == 16_000


def test_funcube_needs_a_narrow_capture():
    # 2.4 kHz signal + 2 x 3.7 kHz Doppler -> well under the 46.9 kS/s stage
    rate = _rate([FUNCUBE])
    assert MIN_NETWORK_RATE <= rate < 46_875


def test_lora_keeps_enough_bandwidth():
    rate = _rate([WREN_LORA])
    assert rate > 125_000 + 2 * max_doppler_hz(137.925e6)
    assert rate <= MAX_NETWORK_RATE


def test_plan_never_exceeds_configured_rate():
    assert plan_network_sample_rate(30_000, [GMSK_9K6]) == 30_000
    assert plan_network_sample_rate(228_000, [FUNCUBE]) == required_capture_rate([FUNCUBE])


def test_plan_without_transmitters_caps_at_network_maximum():
    assert plan_network_sample_rate(2_048_000, []) == MAX_NETWORK_RATE
    assert plan_network_sample_rate(100_000, []) == 100_000


def _rate(txs) -> float:
    rate = required_capture_rate(txs)
    assert rate is not None
    return rate


def test_span_of_two_transmitters_is_included():
    a = dict(FUNCUBE)
    b = dict(FUNCUBE, downlink_low=145_955_000)  # 20 kHz apart
    assert _rate([a, b]) >= 1.25 * 20_000 + _rate([a]) * 0  # span is covered
    assert _rate([a, b]) > _rate([a])
