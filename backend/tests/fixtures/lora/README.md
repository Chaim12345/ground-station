KOSAR 1.5 (NORAD 67264), user-supplied recording on rfbox5:
`KOSAR_1_5_433-436_600MHz-20261023_230232_20261023_200232`.

Source: cf32_le, 2 MS/s, center 436.6 MHz, capture start 2026-10-23T20:02:32Z.
The half-second excerpt starts at sample 38,260,000 (19.13 seconds).
Original excerpt SHA256: `3d7b817f25e5cc9b31a136263956a052f9f139fe11f3c34b66c9d592c405045b`.

`kosar.npz` is a compact regression fixture: translate by +500 kHz,
filter with scipy.signal.firwin(161, 85000, fs=2000000), lfilter, decimate
by 4, quantize real and imaginary components to int16 using the stored scale.
Reconstruct as `(iq.astype(float32) * scale).view(complex64)`.
Result: 250,000 samples at 500 kS/s, centered at 436.1 MHz.

The expected 107-byte packet in `expected.json` was independently recovered
from the original IQ with gr-lora_sdr, with a valid payload CRC.
PHY: SF8, BW125 kHz, CR4/5, sync 0x34, explicit header, normal IQ, auto LDRO.
No satellite application-field interpretations are assumed by these tests.
