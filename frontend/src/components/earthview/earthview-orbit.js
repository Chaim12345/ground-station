/**
 * @license
 * Copyright (c) 2026 Efstratios Goudelis
 *
 * This program is free software: you can redistribute it and/or modify
 * it under the terms of the GNU General Public License as published by
 * the Free Software Foundation, either version 3 of the License, or
 * (at your option) any later version.
 *
 * This program is distributed in the hope that it will be useful,
 * but WITHOUT ANY WARRANTY; without even the implied warranty of
 * MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the
 * GNU General Public License for more details.
 *
 * You should have received a copy of the GNU General Public License
 * along with this program. If not, see <https://www.gnu.org/licenses/>.
 */

import {splitAtDateline} from '../common/tracking-logic.jsx';
import {createSatelliteRecord, propagateSatelliteRecord} from '../../hooks/liveorbit.js';

export const orbitIdentity = (satellite) => JSON.stringify([
    satellite?.norad_id,
    satellite?.tle1,
    satellite?.tle2,
    satellite?.orbit_model_kind ?? satellite?.orbit_format,
    satellite?.orbit_central_body,
    satellite?.orbit_payload,
]);

export function propagateEarthViewSatellite(satellite, date, observer) {
    const {record} = createSatelliteRecord(satellite);
    // Very large drag terms can make SGP4 propagation stall on a map refresh.
    if (record?.bstar && Math.abs(record.bstar) > 0.01) {
        return {record: null, position: null};
    }
    return {record, position: propagateSatelliteRecord(record, date, observer)};
}

export function getEarthViewPaths(record, durationMinutes, stepMinutes = 1, now = new Date()) {
    if (!record || !Number.isFinite(durationMinutes) || !Number.isFinite(stepMinutes) || stepMinutes <= 0) {
        return {past: [], future: []};
    }
    const sample = (direction) => {
        const points = [];
        for (let offset = direction < 0 ? -durationMinutes : 0;
            offset <= (direction < 0 ? 0 : durationMinutes);
            offset += stepMinutes) {
            const position = propagateSatelliteRecord(record, new Date(now.getTime() + offset * 60000));
            if (!position) break;
            points.push({lat: position.lat, lon: position.lon});
        }
        return splitAtDateline(points);
    };
    return {past: sample(-1), future: sample(1)};
}

export function getEarthViewTimeToPeak(record, observer, startDate = new Date(), maxMinutes = 30) {
    const current = propagateSatelliteRecord(record, startDate, observer);
    if (!current || current.el < 0) return null;

    let maximum = current.el;
    let peakSeconds = null;
    for (let seconds = 10; seconds <= maxMinutes * 60; seconds += 10) {
        const future = propagateSatelliteRecord(record, new Date(startDate.getTime() + seconds * 1000), observer);
        if (!future || future.el < 0) break;
        if (future.el > maximum) {
            maximum = future.el;
            peakSeconds = seconds;
        } else if (peakSeconds !== null && future.el < maximum - 0.5) {
            break;
        }
    }
    return peakSeconds;
}
