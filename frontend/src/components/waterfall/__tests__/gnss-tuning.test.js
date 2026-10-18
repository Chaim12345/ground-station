import { describe, expect, it } from 'vitest';
import { getGnssTuningAdvice, getSupportedGnssRate } from '../gnss-tuning.js';

const receiver = {
    vfoFrequency: 1_575_420_000,
    sdrCenterFrequency: 1_575_420_000,
    sdrSampleRate: 2_000_000,
};

describe('GNSS tuning advice', () => {
    it('uses the effective GPS-only defaults when parameter overrides are off', () => {
        const advice = getGnssTuningAdvice({
            ...receiver,
            parametersEnabled: false,
            parameters: { gnss_enable_galileo: true, gnss_enable_beidou: true },
        });

        expect(advice.selectedSignals).toEqual(['GPS L1 C/A']);
        expect(advice.band.centerHz).toBe(1_575_420_000);
        expect(advice.needsTune).toBe(false);
        expect(advice.recommendedRateHz).toBe(2_400_000);
        expect(advice.rateBelowRecommendation).toBe(true);
        expect(getSupportedGnssRate(advice.recommendedRateHz, [2_000_000, 2_400_000, 4_000_000]))
            .toBe(2_400_000);
    });

    it('detects incompatible RF bands and a shifted VFO or SDR center', () => {
        const mixed = getGnssTuningAdvice({
            ...receiver,
            parametersEnabled: true,
            parameters: { gnss_enable_gps: true, gnss_enable_beidou: true },
        });
        expect(mixed.bandCount).toBe(2);
        expect(mixed.band).toBeNull();

        const shifted = getGnssTuningAdvice({
            ...receiver,
            sdrCenterFrequency: 1_575_920_000,
        });
        expect(shifted.needsTune).toBe(true);
    });

    it('recommends a wider capture for Galileo and only supported SDR rates', () => {
        const advice = getGnssTuningAdvice({
            ...receiver,
            parametersEnabled: true,
            parameters: { gnss_enable_gps: false, gnss_enable_galileo: true },
        });
        expect(advice.recommendedRateHz).toBe(4_000_000);
        expect(getSupportedGnssRate(4_000_000, [2_000_000, 2_400_000])).toBeNull();
    });
});
