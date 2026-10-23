import { describe, expect, it } from 'vitest';
import {
    getRunningWaterfallPaths,
    isWaterfallRunningForItem,
} from '../waterfall-task-status.js';

describe('running waterfall task matching', () => {
    it('matches only the recording named by a running generation task', () => {
        const paths = getRunningWaterfallPaths({
            runningTaskIds: ['active', 'unrelated', 'completed'],
            tasks: {
                active: {
                    status: 'running',
                    command: 'generate_waterfall_task',
                    args: ['/recordings/capture'],
                },
                unrelated: {
                    status: 'running',
                    command: 'satdump',
                    args: ['/recordings/other'],
                },
                completed: {
                    status: 'completed',
                    command: 'generate_waterfall_task',
                    args: ['/recordings/finished'],
                },
            },
        });

        expect(isWaterfallRunningForItem({ type: 'recording', name: 'capture' }, paths)).toBe(true);
        expect(isWaterfallRunningForItem({ type: 'recording', name: 'other' }, paths)).toBe(false);
        expect(isWaterfallRunningForItem({ type: 'recording', name: 'finished' }, paths)).toBe(false);
    });

    it('matches an observation recording and its folder from an absolute task path', () => {
        const paths = getRunningWaterfallPaths({
            runningTaskIds: ['active'],
            tasks: {
                active: {
                    status: 'running',
                    command: 'generate_waterfall_task',
                    args: ['/app/data/observations/ISS.gsobs/recordings/capture'],
                },
            },
        });

        expect(isWaterfallRunningForItem({
            type: 'recording', name: 'capture', observation_bundle: 'ISS.gsobs',
        }, paths)).toBe(true);
        expect(isWaterfallRunningForItem({
            type: 'decoded_folder', folder_kind: 'observation', foldername: 'ISS.gsobs',
        }, paths)).toBe(true);
        expect(isWaterfallRunningForItem({
            type: 'recording', name: 'capture', observation_bundle: 'OTHER.gsobs',
        }, paths)).toBe(false);
        expect(isWaterfallRunningForItem({
            type: 'decoded_folder', folder_kind: 'observation', foldername: 'OTHER.gsobs',
        }, paths)).toBe(false);
    });
});
