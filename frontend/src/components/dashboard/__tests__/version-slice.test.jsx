import { configureStore } from '@reduxjs/toolkit';
import { expect, test, vi } from 'vitest';
import reducer, { fetchVersionInfo } from '../version-slice.jsx';

test('stores version information returned through Socket.IO', async () => {
    const payload = {
        version: '1.2.3',
        serverTimeEpochMs: 1_760_000_000_000,
    };
    const emitWithAck = vi.fn().mockResolvedValue({ success: true, data: payload });
    const socket = { connected: true, timeout: vi.fn().mockReturnValue({ emitWithAck }) };
    const store = configureStore({ reducer: { version: reducer } });

    await store.dispatch(fetchVersionInfo({ socket }));

    expect(emitWithAck).toHaveBeenCalledWith('api.call', {
        cmd: 'system.version',
        data: null,
    });
    expect(store.getState().version.data).toEqual(payload);
});

test('reports a disconnected socket without requesting version information', async () => {
    const socket = { connected: false, timeout: vi.fn() };
    const store = configureStore({ reducer: { version: reducer } });

    await store.dispatch(fetchVersionInfo({ socket }));

    expect(socket.timeout).not.toHaveBeenCalled();
    expect(store.getState().version.error).toBe('Socket is disconnected.');
});
