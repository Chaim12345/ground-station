import React from 'react';
import { configureStore } from '@reduxjs/toolkit';
import { Provider } from 'react-redux';
import { fireEvent, render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import { selectRunningRigTransmitters, selectRunningRigTransmitterOptions } from '../../target/transmitter-selectors.js';
import { TransmitterLockSelect } from '../vfo-settings/vfo-transmitter-lock.jsx';

const transmitter = (frequency) => ({
    id: 'shared-transmitter',
    description: 'Beacon',
    downlink_low: frequency,
    downlink_observed_freq: frequency,
    mode: 'FM',
    source: 'SatNOGS',
    alive: true,
});

const targets = [
    { trackerId: 'target-1', type: 'satellite', name: 'ISS', identifier: { norad_id: '25544' }, frequency: 145000000 },
    { trackerId: 'target-2', type: 'mission', name: 'Voyager 1', identifier: { command: 'voyager1' }, frequency: 146000000 },
    { trackerId: 'target-3', type: 'body', name: 'Moon', identifier: { body_id: 'moon' }, frequency: 147000000 },
];

const state = {
    trackerInstances: {
        instances: targets.map(({ trackerId }) => ({ tracker_id: trackerId, is_alive: true })),
    },
    targetSatTrack: {
        trackerId: 'target-1',
        trackerViews: Object.fromEntries(targets.map(({ trackerId, type, name, identifier, frequency }) => [
            trackerId,
            {
                trackingState: { target_type: type, target_name: name, ...identifier },
                satelliteData: { details: { name: type === 'satellite' ? name : '' } },
                rigData: { transmitters: [transmitter(frequency)] },
            },
        ])),
    },
};

describe('VFO transmitter lock target labels', () => {
    it('shows a target badge and name for satellite, mission, and body options', () => {
        const options = selectRunningRigTransmitterOptions(state);
        expect(selectRunningRigTransmitters(state).map(({ trackerTargetName }) => trackerTargetName)).toEqual([
            'ISS', 'Voyager 1', 'Moon',
        ]);

        const store = configureStore({ reducer: () => state });
        render(
            <Provider store={store}>
                <TransmitterLockSelect
                    vfoIndex={1}
                    vfoActive
                    lockedTransmitterId="none"
                    transmitters={options}
                    onVFOPropertyChange={vi.fn()}
                    centerFrequency={146000000}
                    sampleRate={10000000}
                />
            </Provider>,
        );

        fireEvent.mouseDown(screen.getByRole('combobox', { name: /Lock to Transmitter/i }));
        for (const [slot, name] of [[1, 'ISS'], [2, 'Voyager 1'], [3, 'Moon']]) {
            expect(screen.getByRole('option', { name: new RegExp(`T${slot}.*${name}`) })).toBeInTheDocument();
        }
    });

    it('selects the requested target slot when transmitter IDs repeat', () => {
        const store = configureStore({ reducer: () => state });
        const onVFOPropertyChange = vi.fn();
        const options = selectRunningRigTransmitterOptions(state);
        const props = {
            vfoIndex: 1,
            vfoActive: true,
            lockedTransmitterId: 'none',
            transmitters: options,
            onVFOPropertyChange,
            centerFrequency: 146000000,
            sampleRate: 10000000,
        };
        const { rerender } = render(
            <Provider store={store}>
                <TransmitterLockSelect {...props} />
            </Provider>,
        );

        fireEvent.mouseDown(screen.getByRole('combobox', { name: /Lock to Transmitter/i }));
        fireEvent.click(screen.getByRole('option', { name: /T2.*Voyager 1/ }));
        expect(onVFOPropertyChange).toHaveBeenCalledExactlyOnceWith(1, {
            lockedTransmitterId: 'shared-transmitter',
            lockedTransmitterTrackerId: 'target-2',
            frequency: 146000000,
            frequencyOffset: 0,
        });

        rerender(
            <Provider store={store}>
                <TransmitterLockSelect
                    {...props}
                    lockedTransmitterId="shared-transmitter"
                    lockedTransmitterTrackerId="target-2"
                />
            </Provider>,
        );
        expect(screen.getByRole('combobox', { name: /Lock to Transmitter/i })).toHaveTextContent('T2Voyager 1');
    });
});
