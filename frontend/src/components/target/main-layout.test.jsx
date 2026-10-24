import { act, render, waitFor } from '@testing-library/react';
import { configureStore } from '@reduxjs/toolkit';
import { Provider } from 'react-redux';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import TrackingLayout from './main-layout.jsx';

const { gridRender, timelineRender } = vi.hoisted(() => ({
    gridRender: vi.fn(),
    timelineRender: vi.fn(),
}));

vi.mock('react-grid-layout', () => ({
    Responsive: ({ children }) => {
        gridRender();
        return <div>{children}</div>;
    },
    useContainerWidth: () => ({ width: 1200, containerRef: { current: null }, mounted: true }),
}));
vi.mock('../common/socket.jsx', () => ({ useSocket: () => ({ socket: null }) }));
vi.mock('react-i18next', async (importOriginal) => ({
    ...(await importOriginal()),
    useTranslation: () => ({ t: (key) => key }),
}));
vi.mock('../common/common.jsx', () => ({
    StyledIslandParent: ({ children }) => children,
    StyledIslandParentNoScrollbar: ({ children }) => children,
    StyledIslandParentScrollbar: ({ children }) => children,
}));
vi.mock('../common/deferredplaceholder.jsx', () => ({ default: () => null }));
vi.mock('./target-info-island.jsx', () => ({ default: () => null }));
vi.mock('./next-passes.jsx', () => ({ default: () => null }));
vi.mock('./target-view-router.jsx', () => ({ default: () => null }));
vi.mock('./target-selector-bar.jsx', () => ({ default: () => null }));
vi.mock('../dashboard/rotator-control.jsx', () => ({ default: () => null }));
vi.mock('../dashboard/rig-control.jsx', () => ({ default: () => null }));
vi.mock('./timeline-main.jsx', () => ({
    default: (props) => {
        timelineRender(props);
        return null;
    },
}));

const initialTarget = {
    gridEditable: false,
    satelliteId: 1,
    nextPassesHours: 24,
    satelliteData: { details: { name: 'Alpha' } },
};

const targetReducer = (state = initialTarget, action) => {
    if (action.type === 'switchTarget') {
        return {
            ...state,
            satelliteId: 2,
            satelliteData: { details: { name: 'Beta' } },
        };
    }
    if (action.type === 'setEditing') {
        return { ...state, gridEditable: true };
    }
    return state;
};

describe('TrackingLayout render boundaries', () => {
    beforeEach(() => {
        gridRender.mockClear();
        timelineRender.mockClear();
        vi.stubGlobal('requestAnimationFrame', (callback) => window.setTimeout(callback, 0));
        vi.stubGlobal('cancelAnimationFrame', (id) => window.clearTimeout(id));
    });

    afterEach(() => vi.unstubAllGlobals());

    it('updates the timeline on target switch without rebuilding the grid', async () => {
        const store = configureStore({ reducer: { targetSatTrack: targetReducer } });
        render(<Provider store={store}><TrackingLayout /></Provider>);
        await waitFor(() => expect(timelineRender).toHaveBeenCalled());

        const gridRendersBeforeSwitch = gridRender.mock.calls.length;
        act(() => store.dispatch({ type: 'switchTarget' }));

        expect(timelineRender.mock.lastCall[0].satelliteName).toBe('Beta');
        expect(gridRender).toHaveBeenCalledTimes(gridRendersBeforeSwitch);

        act(() => store.dispatch({ type: 'setEditing' }));
        expect(gridRender.mock.calls.length).toBeGreaterThan(gridRendersBeforeSwitch);
    });
});
