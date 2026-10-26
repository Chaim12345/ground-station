import { describe, expect, it } from 'vitest';

import reducer, { fetchRadarView } from './radar-slice.jsx';

describe('radar slice', () => {
    it('tracks the radar view fetch lifecycle', () => {
        let state = reducer(undefined, { type: '@@INIT' });
        expect(state.data).toBeNull();

        state = reducer(state, fetchRadarView.pending('request'));
        expect(state.loading).toBe(true);

        const payload = {
            generated_at: '2026-01-01T00:00:00Z',
            compute_ms: 12,
            stations: [],
            targets: {},
            positions: {},
        };
        state = reducer(state, fetchRadarView.fulfilled(payload, 'request'));
        expect(state.loading).toBe(false);
        expect(state.data).toEqual(payload);
        expect(state.lastFetched).not.toBeNull();

        // rejectWithValue puts the message into action.payload (4th arg: error, requestId, arg, payload)
        state = reducer(state, fetchRadarView.rejected('boom', 'request', undefined, 'boom'));
        expect(state.loading).toBe(false);
        expect(state.error).toBe('boom');
    });
});
