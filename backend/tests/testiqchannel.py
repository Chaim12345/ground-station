"""Chunked channelization must equal processing the whole stream at once."""

import numpy as np
import pytest
from scipy import signal

from demodulators.iqchannel import ChunkChannelizer

pytestmark = pytest.mark.unit

FS = 228000.0
OFFSET = -15000.0
DECIM = 23  # 228000/23 ~ 9913 S/s (8 sps @ 1200 baud, rounded) - not a divisor of chunk size


def _stream(n=16384 * 12, seed=1):
    rng = np.random.default_rng(seed)
    t = np.arange(n) / FS
    tone = np.exp(2j * np.pi * (OFFSET + 300) * t)
    noise = 0.05 * (rng.standard_normal(n) + 1j * rng.standard_normal(n))
    return (tone + noise).astype(np.complex64)


def _reference(x, ch):
    t = np.arange(len(x)) / FS
    shifted = x * np.exp(-2j * np.pi * OFFSET * t)
    return signal.lfilter(ch.taps, 1, shifted)[::DECIM]


def test_chunked_equals_continuous():
    x = _stream()
    ch = ChunkChannelizer()
    ch.configure(DECIM, 12500, FS)
    ref = _reference(x, ch)

    # Irregular chunk sizes, as delivered by the broadcaster after drops/re-tunes.
    sizes = [16384, 16384, 9999, 16384, 7001, 16384]
    out, pos = [], 0
    while pos < len(x):
        n = sizes[len(out) % len(sizes)]
        out.append(ch.process(x[pos : pos + n], OFFSET, FS))
        pos += n
    got = np.concatenate(out)

    assert len(got) == len(ref)
    assert np.max(np.abs(got - ref)) < 1e-4


def test_naive_per_chunk_processing_is_discontinuous():
    """Documents the bug the channelizer fixes (old decoder behaviour)."""
    x = _stream()
    ch = ChunkChannelizer()
    ch.configure(DECIM, 12500, FS)
    ref = _reference(x, ch)

    out = []
    for pos in range(0, len(x), 16384):
        c = x[pos : pos + 16384]
        t = np.arange(len(c)) / FS  # NCO restarts every chunk
        shifted = c * np.exp(-2j * np.pi * OFFSET * t)
        out.append(signal.lfilter(ch.taps, 1, shifted)[::DECIM])  # no zi, phase reset
    naive = np.concatenate(out)
    n = min(len(naive), len(ref))
    assert len(naive) != len(ref) or np.max(np.abs(naive[:n] - ref[:n])) > 0.1


def test_phase_continuous_when_offset_changes():
    """A Doppler-tracking VFO changes frequency, never accumulated phase."""
    ch = ChunkChannelizer()
    ones = np.ones(1000, dtype=np.complex64)
    a = ch.translate(ones, 1000.0, FS)
    b = ch.translate(ones, 1010.0, FS)
    step_a = -2 * np.pi * 1000.0 / FS
    expected_first_b = np.angle(np.exp(1j * step_a * 1000))
    assert abs(np.angle(b[0]) - expected_first_b) < 1e-6
    assert abs(np.angle(a[-1] * np.conj(a[-2])) - step_a) < 1e-6


def test_configure_same_params_keeps_state():
    ch = ChunkChannelizer()
    ch.configure(DECIM, 12500, FS)
    ch.process(_stream(5000), OFFSET, FS)
    assert ch._zi is not None
    zi = ch._zi.copy()
    ch.configure(DECIM, 12500, FS)
    assert ch._zi is not None and np.array_equal(ch._zi, zi)
    ch.configure(DECIM + 1, 12500, FS)
    assert ch._zi is None
