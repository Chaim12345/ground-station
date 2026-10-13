import React from 'react';
import { ThemeProvider } from '@mui/material/styles';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { createMemoryRouter, RouterProvider } from 'react-router';
import { describe, expect, it } from 'vitest';
import '../../../i18n/config.js';
import { setupTheme } from '../../../theme.js';
import { UserAccountPageLayout } from '../settings.jsx';

describe('user account tabs', () => {
    it('switches between preferences and API tokens while keeping their URLs', async () => {
        const router = createMemoryRouter([
            {
                path: '/preferences',
                element: <UserAccountPageLayout activeTab="preferences"><div>Preferences content</div></UserAccountPageLayout>,
            },
            {
                path: '/account/tokens',
                element: <UserAccountPageLayout activeTab="tokens"><div>Token content</div></UserAccountPageLayout>,
            },
        ], { initialEntries: ['/preferences'] });

        render(<ThemeProvider theme={setupTheme('light')}><RouterProvider router={router} /></ThemeProvider>);
        expect(screen.getByRole('tab', { name: 'Preferences' })).toHaveAttribute('aria-selected', 'true');

        fireEvent.click(screen.getByRole('tab', { name: 'API Tokens' }));
        await screen.findByText('Token content');
        expect(router.state.location.pathname).toBe('/account/tokens');
        expect(screen.getByRole('tab', { name: 'API Tokens' })).toHaveAttribute('aria-selected', 'true');

        fireEvent.click(screen.getByRole('tab', { name: 'Preferences' }));
        await waitFor(() => expect(router.state.location.pathname).toBe('/preferences'));
        expect(screen.getByText('Preferences content')).toBeInTheDocument();
    });
});
