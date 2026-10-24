import React from 'react';
import { ThemeProvider } from '@mui/material/styles';
import { fireEvent, render, screen } from '@testing-library/react';
import { expect, it, vi } from 'vitest';
import { setupTheme } from '../../../theme.js';
import FrequencyControlAccordion from '../settings-frequency.jsx';

vi.mock('../frequency-dial.jsx', () => ({ default: () => null }));

it('tunes the exact transmitter choice again when the same destination is picked twice', () => {
    const transmitters = [
        {
            id: 'shared-transmitter',
            uiId: 'target-1:shared-transmitter',
            trackerId: 'target-1',
            trackerTargetName: 'ISS',
            description: 'Beacon',
            downlink_low: 145000000,
            mode: 'FM',
            source: 'SatNOGS',
            alive: true,
        },
        {
            id: 'shared-transmitter',
            uiId: 'target-2:shared-transmitter',
            trackerId: 'target-2',
            trackerTargetName: 'Moon',
            description: 'Beacon',
            downlink_low: 437000000,
            mode: 'FM',
            source: 'SatNOGS',
            alive: true,
        },
    ];
    const onGoToTransmitter = vi.fn();

    render(
        <ThemeProvider theme={setupTheme()}>
            <FrequencyControlAccordion
                expanded
                onAccordionChange={vi.fn()}
                centerFrequency={145000000}
                onCenterFrequencyChange={vi.fn()}
                availableTransmitters={transmitters}
                onGoToTransmitter={onGoToTransmitter}
                selectedOffsetMode="none"
                onOffsetModeChange={vi.fn()}
                selectedOffsetValue="0"
                onOffsetValueChange={vi.fn()}
                isRecording={false}
                selectedSDRId="radio"
                isStreaming={false}
            />
        </ThemeProvider>,
    );

    const goToButton = screen.getByRole('button', { name: /go.to.transmitter/i });
    for (let click = 1; click <= 2; click += 1) {
        fireEvent.click(goToButton);
        fireEvent.click(screen.getByRole('menuitem', { name: /T2.*Moon/ }));
        expect(onGoToTransmitter).toHaveBeenCalledTimes(click);
        expect(onGoToTransmitter).toHaveBeenLastCalledWith(transmitters[1]);
    }
});
