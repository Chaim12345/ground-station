import React from 'react';
import {render, screen, waitFor} from '@testing-library/react';
import {beforeEach, expect, it, vi} from 'vitest';
import '../../i18n/config.js';
import {DecoderConfigSuggestion} from './decoder-config-suggestion.jsx';

const {socket} = vi.hoisted(() => ({
    socket: {emit: vi.fn()},
}));

vi.mock('../common/socket.jsx', () => ({useSocket: () => ({socket})}));

beforeEach(() => {
    socket.emit.mockReset();
    socket.emit.mockImplementation((_event, request, reply) => {
        reply({
            success: true,
            data: {
                baudrate: request.data.transmitter.baud,
                deviation: request.data.transmitter.deviation,
                framing: request.data.transmitter.framing,
                config_source: 'transmitter_metadata',
            },
        });
    });
});

it('refreshes the decoder suggestion when saved transmitter settings change', async () => {
    const satellite = {norad_id: 12345};
    const transmitter = {id: 'tx-1', baud: 200, deviation: 562.5, framing: 'geoscan', mode: 'FSK'};
    const {rerender} = render(
        <DecoderConfigSuggestion show decoderType="fsk" satellite={satellite} transmitter={transmitter}/>
    );

    await waitFor(() => expect(screen.getByText(/562\.5 Hz deviation/)).toBeInTheDocument());
    expect(socket.emit).toHaveBeenCalledWith(
        'api.call',
        expect.objectContaining({data: expect.objectContaining({transmitter})}),
        expect.any(Function),
    );

    rerender(
        <DecoderConfigSuggestion
            show
            decoderType="fsk"
            satellite={satellite}
            transmitter={{...transmitter, deviation: 600}}
        />
    );

    await waitFor(() => expect(screen.getByText(/600 Hz deviation/)).toBeInTheDocument());
    expect(socket.emit).toHaveBeenCalledTimes(2);
});
