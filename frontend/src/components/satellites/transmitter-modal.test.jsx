import React from 'react';
import {configureStore} from '@reduxjs/toolkit';
import {fireEvent, render, screen, waitFor} from '@testing-library/react';
import {Provider} from 'react-redux';
import {expect, it, vi} from 'vitest';
import '../../i18n/config.js';
import satellitesReducer from './satellite-slice.jsx';
import TransmitterModal from './transmitter-modal.jsx';

const socket = vi.hoisted(() => ({emit: vi.fn()}));
vi.mock('../common/socket.jsx', () => ({useSocket: () => ({socket})}));

it('sends the selected TinyGS LoRa profile when editing a transmitter', async () => {
    socket.emit.mockImplementation((_event, request, reply) => {
        reply({success: true, data: [request.data]});
    });
    const store = configureStore({reducer: {satellites: satellitesReducer}});
    const transmitter = {
        id: 'profile-1', description: 'Test Satellite LoRa', source: 'tinygs',
        type: 'Transmitter', status: 'active', alive: true,
        downlinkLow: 436701300, mode: 'LoRa',
        _original: {lora_config: {sf: 10, bw: 203125, cr: 1, sync_word: [18]}},
    };
    render(
        <Provider store={store}>
            <TransmitterModal open onClose={vi.fn()} transmitter={transmitter} satelliteId={44001}/>
        </Provider>
    );

    expect(screen.getByText('203125 (current)')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', {name: 'Save Changes'}));

    await waitFor(() => expect(socket.emit).toHaveBeenCalled());
    const request = socket.emit.mock.calls[0][1];
    expect(request.cmd).toBe('edit-transmitter');
    expect(request.data.lora_config).toEqual({sf: 10, bw: 203125, cr: 1, sync_word: [18]});
    expect(request.data.loraConfig).toBeUndefined();
});
