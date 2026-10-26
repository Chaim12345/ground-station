#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Stateful IQ channelizer shared by raw-IQ packet decoders.

IQ arrives from the IQBroadcaster in independent chunks (~16k samples). A
decoder that frequency-translates and decimates each chunk in isolation
breaks the signal at every chunk boundary:

- restarting the NCO time base at t=0 inserts a carrier phase jump
  (offset_hz * chunk_len / fs) that Costas/FLL loops see as a phase hit;
- filtering without carrying FIR state zero-pads every chunk (transient);
- slicing ``[::D]`` per chunk resets the downsampling phase, inserting or
  dropping a sample whenever chunk_len % D != 0 (timing slip).

At ~10 chunks/s each of these can corrupt a long frame. The AO-40 FEC frame
(FUNcube) is ~4.3 s long, so it spans ~40 chunk boundaries and could never
survive. This class keeps oscillator phase, FIR history and decimation phase
continuous so the decoder sees one unbroken sample stream.
"""

from __future__ import annotations

from typing import Optional

import numpy as np
from scipy import signal


def design_decimation_filter(bandwidth: float, sample_rate: float) -> np.ndarray:
    """Low-pass FIR passing ``bandwidth`` (two-sided) at ``sample_rate``."""
    cutoff = max(1.0, min(bandwidth / 2.0, sample_rate / 2.0 * 0.99))
    transition = max(bandwidth * 0.1, 1.0)
    numtaps = min(1001, int(sample_rate / transition) | 1)
    return signal.firwin(numtaps, cutoff, fs=sample_rate)


class ChunkChannelizer:
    """Phase/state-continuous frequency translation + FIR decimation."""

    def __init__(self) -> None:
        self.translation_phase = 0.0
        self.taps: Optional[np.ndarray] = None
        self.decimation = 1
        self._zi: Optional[np.ndarray] = None
        self._phase = 0  # index of the next kept sample in the following chunk

    def configure(self, decimation: int, bandwidth: float, sample_rate: float) -> None:
        """(Re)design the decimator. Resets filter history only on change."""
        decimation = max(1, int(decimation))
        taps = design_decimation_filter(bandwidth, sample_rate) if decimation > 1 else None
        changed = decimation != self.decimation or (
            (taps is None) != (self.taps is None)
            or (taps is not None and self.taps is not None and not np.array_equal(taps, self.taps))
        )
        self.decimation = decimation
        self.taps = taps
        if changed:
            self._zi = None
            self._phase = 0

    def reset(self) -> None:
        """Forget all stream state (call on sample-rate change or a known gap)."""
        self.translation_phase = 0.0
        self._zi = None
        self._phase = 0

    def translate(self, samples: np.ndarray, offset_hz: float, sample_rate: float) -> np.ndarray:
        """Shift ``offset_hz`` to DC, keeping NCO phase continuous across calls.

        The offset may change between chunks (Doppler-tracked VFO); only the
        frequency changes, never the accumulated phase.
        """
        if offset_hz == 0:
            return samples
        step = -2.0 * np.pi * offset_hz / sample_rate
        phases = self.translation_phase + step * np.arange(len(samples), dtype=np.float64)
        self.translation_phase = float((self.translation_phase + step * len(samples)) % (2 * np.pi))
        return (samples * np.exp(1j * phases)).astype(np.complex64)

    def decimate(self, samples: np.ndarray) -> np.ndarray:
        """FIR-filter and downsample with history + decimation phase preserved."""
        if self.decimation <= 1 or self.taps is None:
            return np.asarray(samples, dtype=np.complex64)
        if self._zi is None:
            self._zi = np.zeros(len(self.taps) - 1, dtype=np.complex128)
        filtered, self._zi = signal.lfilter(self.taps, 1, samples, zi=self._zi)
        start = self._phase
        # Next chunk resumes exactly D samples after the last kept sample.
        self._phase = (start - len(samples)) % self.decimation
        return np.asarray(filtered[start :: self.decimation], dtype=np.complex64)

    def process(self, samples: np.ndarray, offset_hz: float, sample_rate: float) -> np.ndarray:
        """translate() then decimate()."""
        return self.decimate(self.translate(samples, offset_hz, sample_rate))
