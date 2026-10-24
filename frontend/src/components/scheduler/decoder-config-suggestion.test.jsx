import React from 'react';
import {fireEvent, render, screen, waitFor} from '@testing-library/react';
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

it('shows TinyGS LoRa profile values and refreshes when the profile changes', async () => {
    socket.emit.mockImplementation((_event, request, reply) => {
        const transmitter = request.data.transmitter;
        reply({
            success: true,
            data: {
                framing: 'lora', config_source: 'transmitter_metadata', transmitter,
                ...transmitter.lora_config,
            },
        });
    });
    const onApply = vi.fn();
    const transmitter = {
        id: 'tinygs-1', mode: 'LoRa', source: 'tinygs',
        lora_config: {sf: 10, bw: 250000, cr: 1, sync_word: [18], preamble_len: 8},
    };
    const {rerender} = render(
        <DecoderConfigSuggestion show decoderType="lora" transmitter={transmitter} onApply={onApply}/>
    );

    await waitFor(() => expect(screen.getByText(/SF10.*BW250k.*CR4\/5/)).toBeInTheDocument());
    expect(screen.getByText(/TinyGS profile supplies SF, bandwidth, coding rate, sync word, preamble/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', {name: 'Apply'}));
    expect(onApply).toHaveBeenCalledWith(expect.objectContaining({sf: 10, bw: 250000}));

    rerender(
        <DecoderConfigSuggestion show decoderType="lora" onApply={onApply}
                                 transmitter={{...transmitter, lora_config: {...transmitter.lora_config, sf: 9}}}/>
    );
    await waitFor(() => expect(screen.getByText(/SF9.*BW250k/)).toBeInTheDocument());
    expect(socket.emit).toHaveBeenCalledTimes(2);
});
