import { configureStore } from '@reduxjs/toolkit';
import { act, fireEvent } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { renderWithProviders } from '../../../test/test-utils.jsx';
import RotaryEncoder from '../rotator-encoder.jsx';
import vfoReducer, { setVFOProperty, setVfoActive } from '../vfo-marker/vfo-slice.jsx';

vi.mock('react-i18next', async (importOriginal) => ({
    ...await importOriginal(),
    useTranslation: () => ({ t: (key, options) => options?.defaultValue ?? key }),
}));

afterEach(() => {
    vi.restoreAllMocks();
});

describe('RotaryEncoder tuning', () => {
    it('tunes from the latest VFO frequency after another control moves the marker', () => {
        const store = configureStore({ reducer: { vfo: vfoReducer } });
        store.dispatch(setVfoActive(1));
        store.dispatch(setVFOProperty({
            vfoNumber: 1,
            updates: { frequency: 145_000_000, stepSize: 1000 },
        }));

        vi.spyOn(HTMLElement.prototype, 'getBoundingClientRect').mockReturnValue({
            width: 200,
            height: 200,
            top: 0,
            left: 0,
            right: 200,
            bottom: 200,
            x: 0,
            y: 0,
            toJSON: () => {},
        });

        const { container } = renderWithProviders(<RotaryEncoder vfoNumber={1} />, { store });
        const thumbPit = container.querySelector('circle[cx="100"][cy="50"]');

        // Simulate a waterfall marker drag that updates only the frequency.
        act(() => store.dispatch(setVFOProperty({
            vfoNumber: 1,
            updates: { frequency: 145_100_000 },
        })));

        fireEvent.mouseDown(thumbPit, { clientX: 100, clientY: 50 });
        fireEvent.mouseMove(document, { clientX: 110, clientY: 50 });
        expect(store.getState().vfo.vfoMarkers[1].frequency).toBe(145_102_000);

        fireEvent.mouseMove(document, { clientX: 120, clientY: 50 });
        expect(store.getState().vfo.vfoMarkers[1].frequency).toBe(145_104_000);
        fireEvent.mouseUp(document);
    });
});
