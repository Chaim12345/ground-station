"""Regression checks for continuous GNSS IQ frequency translation."""

import queue

import numpy as np
import pytest

from demodulators.gnsssdrdecoder import GNSSSdrDecoder
from pipeline.config.decoderconfigservice import DecoderConfigService


@pytest.mark.parametrize(
    ("first_offset_hz", "second_offset_hz"),
    [
        (300_000, 300_000),
        (300_000, 250_000),
        (300_000, 0),
        (300_000, -125_000),
    ],
)
def test_frequency_translation_keeps_phase_across_chunks(first_offset_hz, second_offset_hz):
    decoder = GNSSSdrDecoder(
        iq_queue=queue.Queue(),
        data_queue=queue.Queue(),
        session_id="phase-test",
        config=DecoderConfigService().get_config("gnss"),
        vfo=1,
    )
    sample_rate = 2_000_000
    first_length = 4_097  # Intentionally not a whole number of mixer cycles.
    second_length = 607

    first_phase_step = 2.0 * np.pi * first_offset_hz / sample_rate
    second_phase_step = 2.0 * np.pi * second_offset_hz / sample_rate
    first = np.exp(1j * first_phase_step * np.arange(first_length)).astype(np.complex64)
    second = np.exp(
        1j * (first_phase_step * first_length + second_phase_step * np.arange(second_length))
    ).astype(np.complex64)

    centered_first = decoder._frequency_translate(first, first_offset_hz, sample_rate)
    centered_second = decoder._frequency_translate(second, second_offset_hz, sample_rate)

    # One continuous input tone must produce the same constant baseband value
    # on both sides of the IQ chunk boundary, even if the VFO shifts there.
    np.testing.assert_allclose(centered_first, 1.0 + 0.0j, rtol=0, atol=1e-6)
    np.testing.assert_allclose(centered_second, 1.0 + 0.0j, rtol=0, atol=1e-6)
    assert 0.0 <= decoder._translation_phase_rad < 2.0 * np.pi
