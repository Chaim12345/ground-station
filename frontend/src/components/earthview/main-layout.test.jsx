import React from 'react';
import { configureStore } from '@reduxjs/toolkit';
import { Provider, useSelector } from 'react-redux';
import { act, fireEvent, render, screen } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import EarthViewLayout from './main-layout.jsx';

const { gridRender, mapRender, retarget, assignmentResolver, socket } = vi.hoisted(() => ({
    gridRender: vi.fn(),
    mapRender: vi.fn(),
    retarget: vi.fn(),
    assignmentResolver: { current: null },
    socket: {},
}));

vi.mock('react-grid-layout', () => ({
    Responsive: ({ children }) => {
        gridRender();
        return <div>{children}</div>;
    },
    useContainerWidth: () => ({ width: 1200, containerRef: { current: null }, mounted: true }),
}));
vi.mock('../common/common.jsx', () => ({
    StyledIslandParent: ({ children }) => children,
    StyledIslandParentNoScrollbar: ({ children }) => children,
    StyledIslandParentScrollbar: ({ children }) => children,
}));
vi.mock('../common/socket.jsx', () => ({ useSocket: () => ({ socket }) }));
vi.mock('./earthview-map-container.jsx', () => ({
    default: ({ handleSetTrackingOnBackend }) => {
        mapRender();
        return <button onClick={() => handleSetTrackingOnBackend({ noradId: 123, satelliteName: 'Sat' })}>Retarget</button>;
    },
}));
vi.mock('../target/use-target-rotator-selection-dialog.jsx', () => ({
    useTargetRotatorSelectionDialog: () => {
        // The real dialog subscribes to tracker views, even while closed.
        useSelector(state => state.targetSatTrack);
        return {
            dialog: null,
            requestRotatorForTarget: () => new Promise(resolve => { assignmentResolver.current = resolve; }),
        };
    },
}));
vi.mock('../target/target-slice.jsx', () => ({
    setTrackerId: value => ({ type: 'setTrackerId', payload: value }),
    setRotator: value => ({ type: 'setRotator', payload: value }),
    setTrackingStateInBackend: value => {
        retarget(value);
        return () => ({ unwrap: () => Promise.resolve({}) });
    },
}));
vi.mock('./satellite-passes.jsx', () => ({ default: () => null }));
vi.mock('./satellite-info.jsx', () => ({ default: () => null }));
vi.mock('./satellites-table.jsx', () => ({ default: () => null }));
vi.mock('./earthview-pass-timeline.jsx', () => ({ default: () => null }));
vi.mock('./satellite-group-selector-bar.jsx', () => ({ default: () => null }));

const initialState = {
    earthViewTrack: { gridEditable: false, selectedSatGroupId: 'group-1' },
    targetSatTrack: {
        trackingState: { group_id: 'fallback' },
        trackerViews: { 'target-1': { trackingState: { transmitter_id: 'tx-1' }, selectedRadioRig: 'rig-1' } },
    },
    trackerInstances: { instances: [{ tracker_id: 'target-1' }] },
};

const reducer = (state = initialState, action) => {
    switch (action.type) {
        case 'switchGroup':
            return { ...state, earthViewTrack: { ...state.earthViewTrack, selectedSatGroupId: action.payload } };
        case 'telemetry':
            return { ...state, targetSatTrack: action.payload };
        case 'editGrid':
            return { ...state, earthViewTrack: { ...state.earthViewTrack, gridEditable: true } };
        default:
            return state;
    }
};

describe('Earth-view grid render boundary', () => {
    beforeEach(() => {
        gridRender.mockClear();
        mapRender.mockClear();
        retarget.mockClear();
        assignmentResolver.current = null;
    });

    it('keeps the grid stable across group and tracker updates, then uses the latest retarget state', async () => {
        const store = configureStore({ reducer });
        render(<Provider store={store}><EarthViewLayout /></Provider>);
        const initialGridRenders = gridRender.mock.calls.length;
        const initialMapRenders = mapRender.mock.calls.length;

        fireEvent.click(screen.getByRole('button', { name: 'Retarget' }));
        act(() => store.dispatch({ type: 'switchGroup', payload: 'group-2' }));
        act(() => store.dispatch({
            type: 'telemetry',
            payload: {
                ...initialState.targetSatTrack,
                trackerViews: {
                    'target-1': { trackingState: { transmitter_id: 'tx-2' }, selectedRadioRig: 'rig-2' },
                },
            },
        }));

        expect(gridRender).toHaveBeenCalledTimes(initialGridRenders);
        expect(mapRender).toHaveBeenCalledTimes(initialMapRenders);

        await act(async () => {
            assignmentResolver.current({ trackerId: 'target-1', rotatorId: 'rot-1', rigId: 'rig-choice' });
        });
        expect(retarget).toHaveBeenCalledTimes(1);
        expect(retarget.mock.calls[0][0].data).toMatchObject({
            group_id: 'group-2',
            rig_id: 'rig-2',
            transmitter_id: 'tx-2',
            norad_id: 123,
        });

        act(() => store.dispatch({ type: 'editGrid' }));
        expect(gridRender.mock.calls.length).toBeGreaterThan(initialGridRenders);
    });
});
