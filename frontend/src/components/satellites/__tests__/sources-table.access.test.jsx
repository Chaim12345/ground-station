import { act, renderHook, waitFor } from '@testing-library/react';
import { configureStore } from '@reduxjs/toolkit';
import { Provider } from 'react-redux';
import { expect, it, vi } from 'vitest';

import { useAdminOrbitalSources } from '../sources-table.jsx';
import sourcesReducer from '../sources-slice.jsx';

it('loads source credentials only while an admin has the Sources page open', async () => {
    const listeners = new Map();
    const socket = {
        connected: true,
        emit: vi.fn((_event, _payload, acknowledge) => {
            acknowledge({ success: true, data: [{ id: 'source-1', password: 'synthetic-secret' }] });
        }),
        on: vi.fn((event, handler) => listeners.set(event, handler)),
        off: vi.fn((event, handler) => {
            if (listeners.get(event) === handler) listeners.delete(event);
        }),
    };
    const authReducer = (state = { authenticated: true, user: { role: 'operator' } }, action) => (
        action.type === 'test/setRole'
            ? { authenticated: true, user: { role: action.payload } }
            : state
    );
    const testStore = configureStore({
        reducer: {
            auth: authReducer,
            dashboard: (state = { connected: true }) => state,
            tleSources: sourcesReducer,
        },
    });
    const wrapper = ({ children }) => <Provider store={testStore}>{children}</Provider>;
    const { unmount } = renderHook(() => useAdminOrbitalSources(socket), { wrapper });

    expect(socket.emit).not.toHaveBeenCalled();
    expect(listeners.has('sat-sync-events')).toBe(false);

    act(() => testStore.dispatch({ type: 'test/setRole', payload: 'admin' }));
    await waitFor(() => expect(socket.emit).toHaveBeenCalledTimes(1));
    expect(socket.emit.mock.calls[0][1].cmd).toBe('get-orbital-sources');
    await waitFor(() => expect(testStore.getState().tleSources.tleSources[0].password).toBe('synthetic-secret'));

    act(() => listeners.get('sat-sync-events')({ status: 'complete' }));
    await waitFor(() => expect(socket.emit).toHaveBeenCalledTimes(2));

    act(() => testStore.dispatch({ type: 'test/setRole', payload: 'operator' }));
    expect(testStore.getState().tleSources.tleSources).toEqual([]);
    expect(listeners.has('sat-sync-events')).toBe(false);
    expect(socket.emit).toHaveBeenCalledTimes(2);

    act(() => testStore.dispatch({ type: 'test/setRole', payload: 'admin' }));
    await waitFor(() => expect(testStore.getState().tleSources.tleSources[0].password).toBe('synthetic-secret'));
    unmount();
    expect(testStore.getState().tleSources.tleSources).toEqual([]);
});
