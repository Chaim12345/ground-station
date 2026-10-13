import { configureStore } from '@reduxjs/toolkit';
import { expect, test, vi } from 'vitest';
import reducer, { fetchUpdateCheck } from '../update-slice.jsx';

const createStore = () => configureStore({ reducer: { updateCheck: reducer } });

test('forces a fresh update check and stores successful release status', async () => {
    const payload = {
        currentVersion: '1.2.0',
        latestVersion: '1.3.0',
        latestTag: 'v1.3.0',
        latestUrl: 'https://github.com/sgoudelis/ground-station/releases/tag/v1.3.0',
        publishedAt: '2026-10-07T10:00:00Z',
        checkedAt: '2026-10-07T11:00:00Z',
        isUpdateAvailable: true,
    };
    const emitWithAck = vi.fn().mockResolvedValue({ success: true, data: payload });
    const socket = { connected: true, timeout: vi.fn().mockReturnValue({ emitWithAck }) };
    const store = createStore();

    await store.dispatch(fetchUpdateCheck({ socket, force: true }));

    expect(socket.timeout).toHaveBeenCalledWith(15000);
    expect(emitWithAck).toHaveBeenCalledWith('api.call', {
        cmd: 'system.update_check',
        data: { refresh: true },
    });
    expect(store.getState().updateCheck.data).toEqual(payload);
    expect(store.getState().updateCheck.error).toBeNull();
    expect(store.getState().updateCheck.lastChecked).toEqual(expect.any(Number));
});

test('stores the backend error returned by a failed GitHub check', async () => {
    const emitWithAck = vi.fn().mockResolvedValue({
        success: false,
        error: 'Unable to check GitHub Releases: connection timed out',
    });
    const socket = { connected: true, timeout: vi.fn().mockReturnValue({ emitWithAck }) };
    const store = createStore();

    await store.dispatch(fetchUpdateCheck({ socket }));

    expect(emitWithAck).toHaveBeenCalledWith('api.call', {
        cmd: 'system.update_check',
        data: { refresh: false },
    });
    const state = store.getState().updateCheck;
    expect(state.loading).toBe(false);
    expect(state.error).toBe('Unable to check GitHub Releases: connection timed out');
    expect(state.lastAttempted).toEqual(expect.any(Number));
    expect(state.lastChecked).toBeNull();
});
