import React from 'react';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import '../../i18n/config.js';
import ApiTokensPage from './tokens.jsx';

describe('API Tokens page', () => {
    afterEach(() => vi.unstubAllGlobals());

    it('shows a newly created secret once and keeps it out of the token list', async () => {
        const secret = 'gs_pat_test.secret-once';
        const token = {
            id: 'token-1', name: 'Home Assistant', hint: 'gs_pat_test…',
            scopes: ['station:read'], status: 'active', created_at: '2026-10-13T00:00:00Z',
            expires_at: null, last_used_at: null,
        };
        let created = false;
        const fetch = vi.fn(async (path, options = {}) => {
            if (path.endsWith('/scopes')) {
                return { ok: true, json: async () => ({ scopes: [{ id: 'station:read', description: 'Read station' }] }) };
            }
            if (path === '/api/auth/tokens' && options.method === 'POST') {
                created = true;
                return { ok: true, json: async () => ({ token: secret, data: token }) };
            }
            return { ok: true, json: async () => ({ data: created ? [token] : [] }) };
        });
        vi.stubGlobal('fetch', fetch);

        render(<ApiTokensPage />);
        await screen.findByText('You have no API tokens yet.');
        fireEvent.click(screen.getByRole('button', { name: 'Create token' }));
        fireEvent.change(screen.getByLabelText('Name'), { target: { value: 'Home Assistant' } });
        expect(screen.queryByLabelText('Current password')).not.toBeInTheDocument();
        fireEvent.click(screen.getByRole('button', { name: 'Continue' }));
        expect(screen.getByRole('heading', { name: 'Verify your identity' })).toBeInTheDocument();
        expect(screen.getByText('API tokens grant access to your station. Enter your current password to confirm this token.')).toBeInTheDocument();
        fireEvent.change(screen.getByLabelText('Current password'), { target: { value: 'password123' } });
        fireEvent.click(screen.getByRole('button', { name: 'Verify and create token' }));

        await waitFor(() => expect(screen.getByDisplayValue(secret)).toBeInTheDocument());
        const post = fetch.mock.calls.find(([path, options]) => path === '/api/auth/tokens' && options.method === 'POST');
        expect(JSON.parse(post[1].body)).toMatchObject({ name: 'Home Assistant', password: 'password123', scopes: ['station:read'] });

        fireEvent.click(screen.getByRole('button', { name: 'Done' }));
        await waitFor(() => expect(screen.queryByDisplayValue(secret)).not.toBeInTheDocument());
        expect(screen.getByText('Home Assistant')).toBeInTheDocument();
        expect(screen.queryByText(secret)).not.toBeInTheDocument();
    });

    it('returns to token settings without creating a token when verification is canceled', async () => {
        const fetch = vi.fn(async (path) => path.endsWith('/scopes')
            ? { ok: true, json: async () => ({ scopes: [{ id: 'station:read', description: 'Read station' }] }) }
            : { ok: true, json: async () => ({ data: [] }) });
        vi.stubGlobal('fetch', fetch);

        render(<ApiTokensPage />);
        await screen.findByText('You have no API tokens yet.');
        fireEvent.click(screen.getByRole('button', { name: 'Create token' }));
        fireEvent.change(screen.getByLabelText('Name'), { target: { value: 'Home Assistant' } });
        fireEvent.click(screen.getByRole('button', { name: 'Continue' }));
        fireEvent.change(screen.getByLabelText('Current password'), { target: { value: 'password123' } });
        fireEvent.click(screen.getByRole('button', { name: 'Back' }));

        expect(screen.getByLabelText('Name')).toHaveValue('Home Assistant');
        await waitFor(() => expect(screen.queryByLabelText('Current password')).not.toBeInTheDocument());
        expect(fetch.mock.calls.some(([, options]) => options?.method === 'POST')).toBe(false);

        fireEvent.click(screen.getByRole('button', { name: 'Continue' }));
        expect(screen.getByLabelText('Current password')).toHaveValue('');
    });
});
