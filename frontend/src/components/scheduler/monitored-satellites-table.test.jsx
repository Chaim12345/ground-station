/**
 * @license
 * Copyright (c) 2026 Efstratios Goudelis
 *
 * This program is free software: you can redistribute it and/or modify
 * it under the terms of the GNU General Public License as published by
 * the Free Software Foundation, either version 3 of the License, or
 * (at your option) any later version.
 */

import React from 'react';
import { act, render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { configureStore } from '@reduxjs/toolkit';
import { Provider } from 'react-redux';
import { afterEach, expect, it, vi } from 'vitest';
import '../../i18n/config.js';
import schedulerReducer from './scheduler-slice.jsx';
import MonitoredSatellitesTable from './monitored-satellites-table.jsx';

const socket = vi.hoisted(() => ({ emit: vi.fn() }));

vi.mock('../common/socket.jsx', () => ({
    useSocket: () => ({ socket }),
}));

vi.mock('../../hooks/useUserTimeSettings.jsx', () => ({
    useUserTimeSettings: () => ({ timezone: 'UTC', locale: 'en' }),
}));

vi.mock('@mui/x-data-grid', async () => {
    const ReactModule = await import('react');
    return {
        gridClasses: { cell: 'MuiDataGrid-cell', columnHeader: 'MuiDataGrid-columnHeader' },
        DataGrid: () => ReactModule.createElement('div'),
    };
});

afterEach(() => {
    socket.emit.mockReset();
    vi.restoreAllMocks();
});

it('keeps regeneration busy until its acknowledgement and permits a retry after failure', async () => {
    const user = userEvent.setup();
    const satellite = { id: 'sat-1', enabled: true, satellite: { name: 'Test satellite' } };
    const generationCallbacks = [];
    vi.spyOn(console, 'error').mockImplementation(() => {});
    vi.spyOn(console, 'log').mockImplementation(() => {});

    socket.emit.mockImplementation((_event, request, acknowledge) => {
        if (request.cmd === 'get-monitored-satellites') {
            acknowledge({ success: true, data: [satellite] });
        } else if (request.cmd === 'regenerate-observations' && request.data.dry_run) {
            acknowledge({
                success: true,
                dry_run: true,
                current_strategy: 'priority',
                conflicting_passes: [],
                no_conflict_passes: [],
            });
        } else if (request.cmd === 'regenerate-observations') {
            generationCallbacks.push(acknowledge);
        }
    });

    const store = configureStore({
        reducer: {
            scheduler: schedulerReducer,
            rotators: () => ({ rotators: [] }),
        },
    });
    render(<Provider store={store}><MonitoredSatellitesTable /></Provider>);

    await waitFor(() => expect(store.getState().scheduler.monitoredSatellites).toHaveLength(1));
    await user.click(screen.getByRole('button', { name: 'Regenerate All Enabled' }));
    const confirmButton = await screen.findByRole('button', { name: 'Confirm & Generate' });
    await user.click(confirmButton);

    expect(generationCallbacks).toHaveLength(1);
    expect(confirmButton).toBeDisabled();
    expect(confirmButton).toContainElement(screen.getByRole('progressbar'));
    expect(screen.getByRole('button', { name: 'Cancel' })).toBeDisabled();

    await act(async () => generationCallbacks[0]({ success: false, error: 'Generation failed' }));
    expect(confirmButton).toBeEnabled();
    expect(screen.queryByRole('progressbar')).not.toBeInTheDocument();

    await user.click(confirmButton);
    expect(generationCallbacks).toHaveLength(2);
    await act(async () => generationCallbacks[1]({ success: true, data: { generated: 1 } }));
    expect(screen.queryByRole('dialog', { name: 'Regeneration Preview' })).not.toBeInTheDocument();
});
