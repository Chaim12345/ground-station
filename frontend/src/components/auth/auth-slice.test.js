import { afterEach, describe, expect, it, vi } from 'vitest';
import { configureStore } from '@reduxjs/toolkit';

import reducer, {
    createUser,
    deleteUser,
    fetchUsers,
    loadAuthStatus,
    loginUser,
    logoutUser,
    resetUserPassword,
    setShowLogoutConfirmation,
    updateUser,
} from './auth-slice.jsx';

const createStore = () => configureStore({ reducer: { auth: reducer } });

describe('auth slice', () => {
    afterEach(() => {
        vi.unstubAllGlobals();
    });

    it('bootstraps the authenticated session and setup recovery mode from the API', async () => {
        const fetch = vi.fn().mockResolvedValue({
            ok: true,
            json: async () => ({
                authenticated: true,
                setup_required: true,
                setup_mode: 'admin_recovery',
                user: { id: 'admin-1', username: 'admin' },
                station: { id: 'station-1', name: 'Home' },
            }),
        });
        vi.stubGlobal('fetch', fetch);
        const store = createStore();

        await store.dispatch(loadAuthStatus());

        expect(fetch).toHaveBeenCalledWith('/api/auth/status', {
            method: 'GET',
            headers: {},
            credentials: 'same-origin',
        });
        expect(store.getState().auth).toMatchObject({
            authenticated: true,
            setupRequired: true,
            setupMode: 'admin_recovery',
            loadingStatus: false,
            statusInitialized: true,
            user: { username: 'admin' },
            station: { name: 'Home' },
        });
    });

    it('keeps a server login error and clears local state even when logout cannot reach the server', async () => {
        const fetch = vi
            .fn()
            .mockResolvedValueOnce({ ok: false, json: async () => ({ detail: 'Invalid credentials' }) })
            .mockRejectedValueOnce(new Error('network unavailable'));
        vi.stubGlobal('fetch', fetch);
        const store = createStore();

        await store.dispatch(loginUser({ username: 'admin', password: 'bad' }));
        await store.dispatch(loadAuthStatus.fulfilled({ authenticated: true, user: { id: 'admin-1' } }, 'seed'));
        await store.dispatch(logoutUser());

        expect(store.getState().auth).toMatchObject({
            authenticated: false,
            user: null,
            loadingAction: false,
            error: 'Invalid credentials',
        });
    });

    it('maintains users, management state, and confirmation preferences', () => {
        let state = reducer(undefined, { type: '@@INIT' });
        state = reducer(state, setShowLogoutConfirmation(false));
        state = reducer(state, createUser.pending('request'));
        state = reducer(state, createUser.fulfilled({ id: 'user-1', username: 'operator', role: 'user' }, 'request'));
        state = reducer(state, updateUser.fulfilled({ id: 'user-1', username: 'operator', role: 'admin' }, 'request'));
        state = reducer(state, deleteUser.fulfilled({ id: 'user-1' }, 'request'));

        expect(state).toMatchObject({
            showLogoutConfirmation: false,
            loadingAction: false,
            error: null,
            users: [],
        });
    });

    it('uses acknowledged Socket.IO commands for admin user management', async () => {
        const fetch = vi.fn();
        vi.stubGlobal('fetch', fetch);
        const socket = {
            connected: true,
            timeout: vi.fn().mockReturnThis(),
            emitWithAck: vi.fn().mockImplementation(async (_event, { cmd, data }) => {
                if (cmd === 'users.list') return { success: true, data: [] };
                if (cmd === 'users.create') return { success: true, data: { id: 'user-1', username: data.username } };
                if (cmd === 'users.update') return { success: true, data: { id: data.user_id, role: data.role } };
                if (cmd === 'users.reset_password') return { success: true, data: { id: data.user_id } };
                if (cmd === 'users.delete') return { success: true, data: { id: data.user_id } };
                throw new Error(`Unexpected command: ${cmd}`);
            }),
        };
        const store = createStore();

        await store.dispatch(fetchUsers({ socket }));
        await store.dispatch(createUser({ socket, username: 'operator', password: 'password123', role: 'operator' }));
        await store.dispatch(updateUser({ socket, userId: 'user-1', role: 'admin', isActive: true }));
        await store.dispatch(resetUserPassword({ socket, userId: 'user-1', password: 'replacement123' }));
        await store.dispatch(deleteUser({ socket, userId: 'user-1' }));

        expect(socket.emitWithAck.mock.calls.map(([, payload]) => payload.cmd)).toEqual([
            'users.list', 'users.create', 'users.update', 'users.reset_password', 'users.delete',
        ]);
        expect(socket.emitWithAck).toHaveBeenCalledWith('api.call', {
            cmd: 'users.update', data: { user_id: 'user-1', role: 'admin', is_active: true },
        });
        expect(store.getState().auth.users).toEqual([]);
        expect(fetch).not.toHaveBeenCalled();
    });

    it('reports socket errors without retrying an uncertain mutation', async () => {
        const socket = {
            connected: true,
            timeout: vi.fn().mockReturnThis(),
            emitWithAck: vi.fn().mockRejectedValue(new Error('operation has timed out')),
        };
        const action = await createStore().dispatch(deleteUser({ socket, userId: 'user-1' }));
        expect(action.payload).toBe('Request timed out. Refresh the users list before retrying.');
        expect(socket.emitWithAck).toHaveBeenCalledTimes(1);
    });
});
