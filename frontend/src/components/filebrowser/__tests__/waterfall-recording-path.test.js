import { describe, expect, it } from 'vitest';
import { getWaterfallRecordingPath } from '../waterfall-recording-path.js';

describe('waterfall task recording path', () => {
    it('uses an observation bundle from the data URL when an older detail payload omits its bundle ID', () => {
        expect(getWaterfallRecordingPath({
            name: 'capture_iq_5',
            download_urls: {
                data: '/observations/ISS_ZARYA_20261004_095338.gsobs/recordings/capture_iq_5.sigmf-data',
            },
        })).toBe('observations/ISS_ZARYA_20261004_095338.gsobs/recordings/capture_iq_5');
    });

    it('keeps standalone captures under the recordings directory', () => {
        expect(getWaterfallRecordingPath({ name: 'capture.sigmf-data' })).toBe('/recordings/capture');
    });
});
