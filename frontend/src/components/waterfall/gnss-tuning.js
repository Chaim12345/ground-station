// Each GNSS-SDR source consumes one baseband centered on a single RF band.
// GLONASS L1 satellites use channels around the nominal center, so a narrow
// capture will only contain a subset of them.
const SIGNALS = [
    { key: 'gnss_enable_gps', band: 'l1', name: 'GPS L1 C/A' },
    { key: 'gnss_enable_galileo', band: 'l1', name: 'Galileo E1B' },
    { key: 'gnss_enable_qzss', band: 'l1', name: 'QZSS L1 C/A' },
    { key: 'gnss_enable_beidou', band: 'b1i', name: 'BeiDou B1I' },
    { key: 'gnss_enable_glonass', band: 'g1', name: 'GLONASS L1 C/A' },
];

const BANDS = {
    l1: { centerHz: 1_575_420_000, label: '1575.420 MHz (L1/E1)' },
    b1i: { centerHz: 1_561_098_000, label: '1561.098 MHz (B1I)' },
    g1: { centerHz: 1_602_000_000, label: '1602.000 MHz nominal (GLONASS L1)' },
};

export const formatGnssFrequency = (frequencyHz) => `${(frequencyHz / 1_000_000).toFixed(3)} MHz`;
export const formatGnssRate = (rateHz) => `${(rateHz / 1_000_000).toFixed(2)} MS/s`;

export function getGnssTuningAdvice({
    parameters = {},
    parametersEnabled = false,
    vfoFrequency,
    sdrCenterFrequency,
    sdrSampleRate,
}) {
    const enabled = SIGNALS.filter((signal) => (
        !parametersEnabled
            ? signal.key === 'gnss_enable_gps'
            : (parameters[signal.key] ?? (signal.key === 'gnss_enable_gps'))
    ));
    const bands = [...new Set(enabled.map((signal) => signal.band))];
    const band = bands.length === 1 ? BANDS[bands[0]] : null;
    const rate = Number(sdrSampleRate);
    const center = Number(sdrCenterFrequency);
    const vfo = Number(vfoFrequency);
    const hasSampleRate = Number.isFinite(rate) && rate > 0;
    const hasCenter = Number.isFinite(center) && center > 0;
    const hasReceiver = hasSampleRate && hasCenter;
    const recommendedRateHz = bands[0] === 'g1' || !band
        ? null
        : enabled.some((signal) => signal.key === 'gnss_enable_beidou')
            ? 4_800_000
            : enabled.some((signal) => signal.key === 'gnss_enable_galileo')
                ? 4_000_000
                : 2_400_000;

    return {
        selectedSignals: enabled.map((signal) => signal.name),
        bandCount: bands.length,
        band,
        recommendedRateHz,
        rateBelowRecommendation: Boolean(band && hasSampleRate && recommendedRateHz && rate < recommendedRateHz),
        // A GNSS signal occupies much more than its carrier. Centering the SDR
        // avoids silently clipping most of it even if the carrier is in band.
        needsTune: Boolean(band && (
            !hasCenter || Math.abs(center - band.centerHz) > 10_000 ||
            !Number.isFinite(vfo) || vfo <= 0 || Math.abs(vfo - band.centerHz) > 10_000
        )),
        hasReceiver,
        hasSampleRate,
        hasCenter,
    };
}

export function getSupportedGnssRate(recommendedRateHz, sampleRateValues = []) {
    if (!recommendedRateHz) return null;
    const values = sampleRateValues.map(Number).filter((rate) => Number.isFinite(rate) && rate >= recommendedRateHz);
    return values.length > 0 ? Math.min(...values) : null;
}
