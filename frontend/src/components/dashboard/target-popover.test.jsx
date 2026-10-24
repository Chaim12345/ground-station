import React from 'react';
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { configureStore } from '@reduxjs/toolkit';
import { Provider } from 'react-redux';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import '../../i18n/config.js';
import SatelliteInfoPopover from './target-popover.jsx';

const { socket, navigate } = vi.hoisted(() => ({
    socket: { connected: false, emit: vi.fn() },
    navigate: vi.fn(),
}));

vi.mock('../common/socket.jsx', () => ({ useSocket: () => ({ socket }) }));
vi.mock('../../hooks/useUserTimeSettings.jsx', () => ({
    useUserTimeSettings: () => ({ timezone: 'UTC', locale: 'en' }),
}));
vi.mock('react-router-dom', async (importOriginal) => ({
    ...(await importOriginal()),
    useNavigate: () => navigate,
}));
vi.mock('../satellites/satellite-edit-dialog.jsx', () => ({
    default: ({ open, onClose, satelliteData }) => open && (
        <div role="dialog" aria-label="Satellite editor">
            <span>{satelliteData?.name}</span>
            <span>{satelliteData?.operator}</span>
            <button type="button" onClick={onClose}>Close editor</button>
        </div>
    ),
}));
vi.mock('../satellites/transmitters-dialog.jsx', () => ({
    default: ({ open, onClose, satelliteData, title }) => open && (
        <div role="dialog" aria-label="Transmitters editor">
            <span>{title}</span>
            <span>{satelliteData?.norad_id}</span>
            <span>{satelliteData?.target_key}</span>
            <button type="button" onClick={onClose}>Close transmitters</button>
        </div>
    ),
}));

const renderPopover = () => {
    const state = {
        trackerInstances: {
            instances: [
                { tracker_id: 'sat-1', target_number: 1 },
                { tracker_id: 'sat-2', target_number: 2 },
                { tracker_id: 'body-1', target_number: 3 },
                { tracker_id: 'mission-1', target_number: 4 },
            ],
        },
        targetSatTrack: {
            trackerId: 'sat-1',
            fleetPassSummaryByTrackerId: {},
            trackerViews: {
                'sat-1': {
                    satelliteData: { details: { norad_id: 111, name: 'First satellite' }, position: { el: 20 } },
                    trackingState: { norad_id: 111 },
                },
                'sat-2': {
                    satelliteData: { details: { norad_id: 222, name: 'Second satellite' }, position: { el: 15 } },
                    trackingState: { norad_id: 222 },
                },
                'body-1': {
                    satelliteData: { details: { target_type: 'body', body_id: 'moon', name: 'Moon' }, position: { el: 30 } },
                    trackingState: { target_type: 'body', body_id: 'moon', target_key: 'body:moon' },
                },
                'mission-1': {
                    satelliteData: { details: { target_type: 'mission', command: '-61', name: 'Voyager 1' }, position: { el: 10 } },
                    trackingState: { target_type: 'mission', command: '-61', target_key: 'mission:-61' },
                },
            },
        },
    };
    const store = configureStore({ reducer: (current = state) => current });
    render(<Provider store={store}><SatelliteInfoPopover /></Provider>);
    fireEvent.click(screen.getByRole('button', { name: /satellite info/i }));
};

describe('dashboard target popover editors', () => {
    beforeEach(() => {
        socket.emit.mockReset();
        navigate.mockReset();
        Object.defineProperty(navigator, 'clipboard', {
            configurable: true,
            value: { writeText: vi.fn().mockResolvedValue(undefined) },
        });
        socket.emit.mockImplementation((_event, request, acknowledge) => {
            if (request.cmd === 'get-satellite') {
                acknowledge({ success: true, data: { details: { norad_id: request.data, operator: 'Catalog operator' } } });
            }
        });
    });

    it('opens the clicked satellite in the shared editor without navigating', () => {
        renderPopover();
        expect(screen.getAllByRole('button', { name: 'Edit satellite' })).toHaveLength(2);
        fireEvent.click(screen.getAllByRole('button', { name: 'Edit satellite' })[1]);

        expect(socket.emit).toHaveBeenCalledWith(
            'api.call',
            { cmd: 'get-satellite', data: 222 },
            expect.any(Function),
        );
        expect(within(screen.getByRole('dialog', { name: 'Satellite editor' })).getByText('Second satellite')).toBeInTheDocument();
        expect(within(screen.getByRole('dialog', { name: 'Satellite editor' })).getByText('Catalog operator')).toBeInTheDocument();
        expect(navigate).not.toHaveBeenCalled();
    });

    it('opens transmitters for the clicked satellite and keeps row navigation available', () => {
        renderPopover();
        expect(screen.getAllByRole('button', { name: 'Edit transmitters' })).toHaveLength(4);
        fireEvent.click(screen.getAllByRole('button', { name: 'Edit transmitters' })[0]);

        const dialog = screen.getByRole('dialog', { name: 'Transmitters editor' });
        expect(within(dialog).getByText('111')).toBeInTheDocument();
        expect(navigate).not.toHaveBeenCalled();

        fireEvent.click(within(dialog).getByRole('button', { name: 'Close transmitters' }));
        fireEvent.click(screen.getByRole('button', { name: /satellite info/i }));
        fireEvent.click(screen.getByRole('button', { name: /Second satellite \(222\)/i }));
        expect(navigate).toHaveBeenCalledWith('/tracking');
    });

    it.each([
        ['body:moon', 2],
        ['mission:-61', 3],
    ])('opens transmitters for %s using its target key', (targetKey, buttonIndex) => {
        renderPopover();
        fireEvent.click(screen.getAllByRole('button', { name: 'Edit transmitters' })[buttonIndex]);

        expect(within(screen.getByRole('dialog', { name: 'Transmitters editor' })).getByText(targetKey)).toBeInTheDocument();
        expect(navigate).not.toHaveBeenCalled();
    });

    it('copies the displayed name and identifier for satellites, bodies, and missions', async () => {
        renderPopover();
        const copyButtons = screen.getAllByRole('button', { name: 'Copy target name and ID' });
        expect(copyButtons).toHaveLength(4);
        copyButtons.forEach((button) => fireEvent.click(button));

        await waitFor(() => expect(navigator.clipboard.writeText).toHaveBeenCalledTimes(4));
        expect(navigator.clipboard.writeText).toHaveBeenNthCalledWith(1, 'First satellite (111)');
        expect(navigator.clipboard.writeText).toHaveBeenNthCalledWith(2, 'Second satellite (222)');
        expect(navigator.clipboard.writeText).toHaveBeenNthCalledWith(3, 'Moon (moon)');
        expect(navigator.clipboard.writeText).toHaveBeenNthCalledWith(4, 'Voyager 1 (-61)');
        expect(navigate).not.toHaveBeenCalled();
    });
});
