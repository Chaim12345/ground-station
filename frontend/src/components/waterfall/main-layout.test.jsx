import {act, render} from '@testing-library/react';
import {configureStore} from '@reduxjs/toolkit';
import {Provider} from 'react-redux';
import {afterEach, beforeEach, describe, expect, it, vi} from 'vitest';
import MainLayout from './main-layout.jsx';

const {gridRender} = vi.hoisted(() => ({gridRender: vi.fn()}));

vi.mock('leaflet-fullscreen/dist/Leaflet.fullscreen.js', () => ({}));
vi.mock('react-grid-layout', () => ({
    Responsive: ({children}) => {
        gridRender();
        return <div>{children}</div>;
    },
    useContainerWidth: () => ({width: 1200, containerRef: {current: null}, mounted: true}),
}));
vi.mock('../common/common.jsx', () => ({
    StyledIslandParentScrollbar: ({children}) => children,
}));
vi.mock('../common/deferredplaceholder.jsx', () => ({default: () => null}));
vi.mock('./waterfall-island.jsx', () => ({default: () => null}));
vi.mock('./settings-column.jsx', () => ({default: () => null}));
vi.mock('./decoded-insights-island.jsx', () => ({default: () => null}));
vi.mock('./render-profiler.jsx', () => ({DevRenderProfiler: ({children}) => children}));

const initialWaterfall = {gridEditable: false, centerFrequency: 100000000};
const waterfallReducer = (state = initialWaterfall, action) => {
    if (action.type === 'tune') {
        return {...state, centerFrequency: action.payload};
    }
    if (action.type === 'editGrid') {
        return {...state, gridEditable: true};
    }
    return state;
};

describe('waterfall grid render boundary', () => {
    beforeEach(() => {
        gridRender.mockClear();
        vi.stubGlobal('requestAnimationFrame', () => 1);
        vi.stubGlobal('cancelAnimationFrame', () => {});
    });

    afterEach(() => vi.unstubAllGlobals());

    it('does not rebuild the grid when the SDR frequency changes', () => {
        const store = configureStore({reducer: {waterfall: waterfallReducer}});
        render(<Provider store={store}><MainLayout/></Provider>);
        const rendersBeforeTune = gridRender.mock.calls.length;

        act(() => store.dispatch({type: 'tune', payload: 145000000}));
        expect(gridRender).toHaveBeenCalledTimes(rendersBeforeTune);

        act(() => store.dispatch({type: 'editGrid'}));
        expect(gridRender.mock.calls.length).toBeGreaterThan(rendersBeforeTune);
    });
});
