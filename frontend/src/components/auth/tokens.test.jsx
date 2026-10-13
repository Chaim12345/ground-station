import React from 'react';
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import '../../i18n/config.js';
import ApiTokensPage from './tokens.jsx';

const { socket } = vi.hoisted(() => ({
    socket: {
        connected: true,
        timeout: vi.fn(),
        emitWithAck: vi.fn(),
        on: vi.fn(),
        off: vi.fn(),
    },
}));

vi.mock('../common/socket.jsx', () => ({ useSocket: () => ({ socket }) }));

const scope = { id: 'station:read', description: 'Read station' };
const token = {
    id: 'token-1', name: 'Home Assistant', hint: 'gs_pat_test…',
    scopes: ['station:read'], status: 'active', created_at: '2026-10-13T00:00:00Z',
    expires_at: null, last_used_at: null,
};

describe('API Tokens page', () => {
    beforeEach(() => {
        socket.connected = true;
        socket.timeout.mockReset().mockReturnValue(socket);
        socket.emitWithAck.mockReset();
        socket.on.mockReset();
        socket.off.mockReset();
    });

    it('shows a newly created secret once and keeps it out of the token list', async () => {
        const secret = 'gs_pat_test.secret-once';
        let created = false;
        socket.emitWithAck.mockImplementation(async (_event, { cmd }) => {
            if (cmd === 'tokens.scopes') return { success: true, data: [scope] };
            if (cmd === 'tokens.create') {
                created = true;
                return { success: true, data: { token: secret, data: token } };
            }
            return { success: true, data: created ? [token] : [] };
        });

        render(<ApiTokensPage />);
        await screen.findByText('You have no API tokens yet.');
        fireEvent.click(screen.getByRole('button', { name: 'Create token' }));
        fireEvent.change(screen.getByLabelText('Name'), { target: { value: 'Home Assistant' } });
        expect(screen.queryByLabelText('Current password')).not.toBeInTheDocument();
        fireEvent.click(screen.getByRole('button', { name: 'Continue' }));
        expect(screen.getByRole('heading', { name: 'Verify your identity' })).toBeInTheDocument();
        fireEvent.change(screen.getByLabelText('Current password'), { target: { value: 'password123' } });
        fireEvent.click(screen.getByRole('button', { name: 'Verify and create token' }));

        await waitFor(() => expect(screen.getByDisplayValue(secret)).toBeInTheDocument());
        expect(socket.emitWithAck).toHaveBeenCalledWith('api.call', {
            cmd: 'tokens.create',
            data: { name: 'Home Assistant', password: 'password123', scopes: ['station:read'], expires_in_days: 365 },
        });
        fireEvent.click(screen.getByRole('button', { name: 'Done' }));
        await waitFor(() => expect(screen.queryByDisplayValue(secret)).not.toBeInTheDocument());
        expect(screen.getByText('Home Assistant')).toBeInTheDocument();
        expect(screen.queryByText(secret)).not.toBeInTheDocument();
    });

    it('returns to token settings without creating a token when verification is canceled', async () => {
        socket.emitWithAck.mockImplementation(async (_event, { cmd }) => ({
            success: true, data: cmd === 'tokens.scopes' ? [scope] : [],
        }));
        render(<ApiTokensPage />);
        await screen.findByText('You have no API tokens yet.');
        fireEvent.click(screen.getByRole('button', { name: 'Create token' }));
        fireEvent.change(screen.getByLabelText('Name'), { target: { value: 'Home Assistant' } });
        fireEvent.click(screen.getByRole('button', { name: 'Continue' }));
        fireEvent.change(screen.getByLabelText('Current password'), { target: { value: 'password123' } });
        fireEvent.click(screen.getByRole('button', { name: 'Back' }));

        expect(screen.getByLabelText('Name')).toHaveValue('Home Assistant');
        await waitFor(() => expect(screen.queryByLabelText('Current password')).not.toBeInTheDocument());
        expect(socket.emitWithAck.mock.calls.some(([, payload]) => payload.cmd === 'tokens.create')).toBe(false);
        fireEvent.click(screen.getByRole('button', { name: 'Continue' }));
        expect(screen.getByLabelText('Current password')).toHaveValue('');
    });

    it('loads token management after the socket reconnects', async () => {
        socket.connected = false;
        socket.emitWithAck.mockImplementation(async (_event, { cmd }) => ({
            success: true, data: cmd === 'tokens.scopes' ? [scope] : [token],
        }));
        render(<ApiTokensPage />);
        expect(screen.getByText('Loading tokens…')).toBeInTheDocument();
        expect(socket.emitWithAck).not.toHaveBeenCalled();

        socket.connected = true;
        socket.on.mock.calls.find(([event]) => event === 'connect')[1]();
        await screen.findByText('Home Assistant');
        expect(socket.emitWithAck).toHaveBeenCalledWith('api.call', {
            cmd: 'tokens.list', data: null,
        });
    });

    it('renames and revokes through Socket.IO', async () => {
        let status = 'active';
        let name = token.name;
        socket.emitWithAck.mockImplementation(async (_event, { cmd, data }) => {
            if (cmd === 'tokens.scopes') return { success: true, data: [scope] };
            if (cmd === 'tokens.rename') name = data.name;
            if (cmd === 'tokens.revoke') status = 'revoked';
            return { success: true, data: [{ ...token, name, status }] };
        });
        render(<ApiTokensPage />);
        await screen.findByText('Home Assistant');
        fireEvent.click(screen.getByRole('button', { name: 'Rename token' }));
        fireEvent.change(screen.getByLabelText('Name'), { target: { value: 'Updated' } });
        fireEvent.click(screen.getByRole('button', { name: 'Save' }));
        await screen.findByText('Updated');
        expect(socket.emitWithAck).toHaveBeenCalledWith('api.call', {
            cmd: 'tokens.rename', data: { token_id: 'token-1', name: 'Updated' },
        });
        const revokeButton = await screen.findByRole('button', { name: 'Revoke token' });
        fireEvent.click(revokeButton);
        fireEvent.click(within(screen.getByRole('dialog', { name: 'Revoke token' })).getByRole('button', { name: 'Revoke token' }));
        await screen.findByText('revoked');
        expect(socket.emitWithAck).toHaveBeenCalledWith('api.call', {
            cmd: 'tokens.revoke', data: { token_id: 'token-1' },
        });
    });
});
