import React from 'react';
import { configureStore } from '@reduxjs/toolkit';
import { Provider } from 'react-redux';
import { act, fireEvent, render, screen } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import SchedulerDialogHost from './scheduler-dialog-host.jsx';

const { observationRender, monitoredRender } = vi.hoisted(() => ({
    observationRender: vi.fn(),
    monitoredRender: vi.fn(),
}));

vi.mock('./observation-form-dialog.jsx', () => ({
    default: ({ onExited }) => {
        observationRender();
        return <div>Observation form<button onClick={onExited}>Finish observation exit</button></div>;
    },
}));
vi.mock('./monitored-satellite-dialog.jsx', () => ({
    default: ({ onExited }) => {
        monitoredRender();
        return <div>Monitored satellite form<button onClick={onExited}>Finish monitored exit</button></div>;
    },
}));

const initialState = { dialogOpen: false, monitoredSatelliteDialogOpen: false, observations: [] };
const schedulerReducer = (state = initialState, action) => {
    switch (action.type) {
        case 'openObservation':
            return { ...state, dialogOpen: true };
        case 'openMonitored':
            return { ...state, monitoredSatelliteDialogOpen: true };
        case 'closeObservation':
            return { ...state, dialogOpen: false };
        case 'updateObservations':
            return { ...state, observations: action.payload };
        default:
            return state;
    }
};

describe('scheduler dialog host', () => {
    beforeEach(() => {
        observationRender.mockClear();
        monitoredRender.mockClear();
    });

    it('mounts forms only while open and ignores unrelated scheduler updates', () => {
        const store = configureStore({ reducer: { scheduler: schedulerReducer } });
        render(<Provider store={store}><SchedulerDialogHost /></Provider>);
        expect(observationRender).not.toHaveBeenCalled();
        expect(monitoredRender).not.toHaveBeenCalled();

        act(() => store.dispatch({ type: 'updateObservations', payload: [{ id: 'obs-1' }] }));
        expect(observationRender).not.toHaveBeenCalled();
        expect(monitoredRender).not.toHaveBeenCalled();

        act(() => store.dispatch({ type: 'openObservation' }));
        expect(screen.getByText('Observation form')).toBeInTheDocument();
        expect(observationRender).toHaveBeenCalledTimes(1);

        act(() => store.dispatch({ type: 'updateObservations', payload: [{ id: 'obs-2' }] }));
        expect(observationRender).toHaveBeenCalledTimes(1);

        act(() => store.dispatch({ type: 'openMonitored' }));
        expect(screen.getByText('Monitored satellite form')).toBeInTheDocument();
        expect(monitoredRender).toHaveBeenCalledTimes(1);

        act(() => store.dispatch({ type: 'closeObservation' }));
        expect(screen.getByText('Observation form')).toBeInTheDocument();
        fireEvent.click(screen.getByRole('button', { name: 'Finish observation exit' }));
        expect(screen.queryByText('Observation form')).not.toBeInTheDocument();
        expect(screen.getByText('Monitored satellite form')).toBeInTheDocument();
    });
});
