import { describe, expect, it } from 'vitest';

import {
    DECODER_SUPPORT,
    getDecoderDefaultParameters,
    getDecoderParameters,
    mapParametersToBackend,
} from '../decoder-parameters.js';

describe('APRS decoder parameters', () => {
    it('offers APRS and keeps the legacy AFSK decoder disabled', () => {
        expect(DECODER_SUPPORT.aprs).toBe(true);
        expect(DECODER_SUPPORT.afsk).toBe(false);
        expect(Object.keys(getDecoderParameters('afsk'))).toHaveLength(0);
    });

    it('uses Bell 202 defaults and maps them to the raw-IQ backend decoder', () => {
        const defaults = getDecoderDefaultParameters('aprs');

        expect(defaults).toEqual({
            aprs_baudrate: 1200,
            aprs_af_carrier: 1700,
            aprs_deviation: 500,
        });
        expect(mapParametersToBackend('aprs', defaults)).toEqual({
            baudrate: 1200,
            af_carrier: 1700,
            deviation: 500,
            framing: 'aprs',
        });
    });
});

describe('Geoscan image decoder parameters', () => {
    it('keeps the receiver profile explicit and maps its Geoscan framing values', () => {
        const defaults = getDecoderDefaultParameters('geoscanimage');

        expect(defaults).toEqual({
            geoscanimage_baudrate: 9600,
            geoscanimage_deviation: 5000,
            geoscanimage_frame_size: 74,
            geoscanimage_syncword_threshold: 4,
            geoscanimage_satellite_id: 9,
        });
        expect(mapParametersToBackend('geoscanimage', defaults)).toEqual({
            baudrate: 9600,
            deviation: 5000,
            framing_params: {
                frame_size: 74,
                syncword_threshold: 4,
                satellite_id: 9,
            },
        });
    });
});

describe('SSTV decoder parameters', () => {
    it('offers only implemented modes and maps a forced mode to the backend', () => {
        const mode = getDecoderParameters('sstv').sstv_mode;

        expect(mode.options.map((option) => option.value)).toEqual([
            'auto',
            'robot36',
            'martin_m1',
            'martin_m2',
            'scottie_s1',
            'scottie_s2',
            'scottie_dx',
            'wraase_sc2_180',
        ]);
        expect(mapParametersToBackend('sstv', { sstv_mode: 'scottie_s2' })).toEqual({
            sstv_mode: 'scottie_s2',
        });
    });
});

describe('GNSS decoder parameters', () => {
    it('defaults to GPS L1 only and keeps other constellations selectable', () => {
        const defaults = getDecoderDefaultParameters('gnss');

        expect(defaults).toMatchObject({
            gnss_enable_gps: true,
            gnss_enable_galileo: false,
            gnss_enable_glonass: false,
            gnss_enable_beidou: false,
            gnss_enable_qzss: false,
        });
        expect(mapParametersToBackend('gnss', defaults)).toMatchObject(defaults);
    });
});
