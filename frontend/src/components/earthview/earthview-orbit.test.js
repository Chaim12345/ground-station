import {describe, expect, it} from 'vitest';
import {createSatelliteRecord, propagateSatelliteRecord} from '../../hooks/liveorbit.js';
import {calculateElevationCurve} from '../../utils/elevation-curve-calculator.js';
import {calculateNeighboringTransmitters} from '../../utils/doppler-calculator.js';
import {getEarthViewPaths, orbitIdentity, propagateEarthViewSatellite} from './earthview-orbit.js';

const observationTime = new Date('2025-03-26T05:25:00Z');
const ommSatellite = {
    norad_id: 100000,
    name: 'OMM-only satellite',
    orbit_model_kind: 'omm',
    orbit_central_body: 'earth',
    orbit_payload: {
        OBJECT_NAME: 'OMM-only satellite',
        OBJECT_ID: '2025-001A',
        EPOCH: '2025-03-26T05:19:34.116960',
        MEAN_MOTION: '15.00555103',
        ECCENTRICITY: '0.000583',
        INCLINATION: '98.3164',
        RA_OF_ASC_NODE: '103.8411',
        ARG_OF_PERICENTER: '20.5667',
        MEAN_ANOMALY: '339.5789',
        EPHEMERIS_TYPE: '0',
        CLASSIFICATION_TYPE: 'U',
        NORAD_CAT_ID: '100000',
        ELEMENT_SET_NO: '999',
        REV_AT_EPOCH: '8655',
        BSTAR: '0.00048021',
        MEAN_MOTION_DOT: '0.00005995',
        MEAN_MOTION_DDOT: '0',
    },
};

describe('OMM-only browser orbit consumers', () => {
    it('propagates Earth View positions and paths and notices element updates', () => {
        const station = {lat: 37.9838, lon: 23.7275, alt: 100};
        const {record, position} = propagateEarthViewSatellite(ommSatellite, observationTime, station);
        const paths = getEarthViewPaths(record, 2, 1, observationTime);
        const changed = {
            ...ommSatellite,
            orbit_payload: {...ommSatellite.orbit_payload, INCLINATION: '98.4'},
        };

        expect(position).toEqual(expect.objectContaining({
            lat: expect.any(Number), lon: expect.any(Number), az: expect.any(Number),
            el: expect.any(Number), range: expect.any(Number),
        }));
        expect(paths.past.flat()).toHaveLength(3);
        expect(paths.future.flat()).toHaveLength(3);
        expect(orbitIdentity(ommSatellite)).not.toBe(orbitIdentity(changed));
    });

    it('produces group pass curves and neighboring transmitter Doppler from OMM', () => {
        const record = createSatelliteRecord(ommSatellite).record;
        const overhead = propagateSatelliteRecord(record, observationTime);
        const curve = calculateElevationCurve(
            ommSatellite,
            {lat: overhead.lat, lon: overhead.lon},
            new Date(observationTime.getTime() - 2 * 60000).toISOString(),
            new Date(observationTime.getTime() + 2 * 60000).toISOString(),
        );
        const neighbors = calculateNeighboringTransmitters(
            [{
                ...ommSatellite,
                transmitters: [{id: 'tx', alive: true, downlink_low: 436888000}],
            }],
            436888000,
            2000000,
            overhead.lat,
            overhead.lon,
            0,
            observationTime,
        );

        expect(curve.length).toBeGreaterThan(0);
        expect(Math.max(...curve.map(point => point.elevation))).toBeGreaterThan(0);
        expect(neighbors).toHaveLength(1);
        expect(neighbors[0].doppler_frequency).not.toBe(436888000);
        const observer = {lat: overhead.lat, lon: overhead.lon, alt: 0};
        const before = propagateSatelliteRecord(
            record, new Date(observationTime.getTime() - 1000), observer,
        ).range;
        const after = propagateSatelliteRecord(
            record, new Date(observationTime.getTime() + 1000), observer,
        ).range;
        const expectedShift = -436888000 * ((after - before) / 2) / 299792.458;
        expect(neighbors[0].doppler_shift).toBeCloseTo(expectedShift, 0);
    });
});
