import React, { useState } from 'react';
import { fireEvent, render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import { ObservationRigFields } from './observation-rig-fields.jsx';

function Form() {
    const [rig, setRig] = useState({ id: null, transmitter_id: '' });
    return (
        <>
            <ObservationRigFields
                rig={rig}
                rigs={[{ id: 'rig-1', name: 'Radio 1' }]}
                transmitters={[
                    { id: 'tx-1', description: 'Downlink', downlink_low: 145800000 },
                    { id: 'tx-2', description: 'Uplink only', downlink_low: 0 },
                ]}
                onChange={setRig}
                t={(key) => ({
                    'scheduler_dialogs.shared.observation_rig': 'Observation rig',
                    'scheduler_dialogs.shared.observation_rig_transmitter': 'Rig tracking transmitter',
                    'scheduler_dialogs.shared.none': 'None',
                }[key] || key)}
            />
            <output data-testid="rig-state">{JSON.stringify(rig)}</output>
        </>
    );
}

describe('observation rig setup', () => {
    it('requires a downlink transmitter when a rig is selected and clears it when removed', () => {
        render(<Form />);
        const rigSelect = screen.getByRole('combobox', { name: 'Observation rig' });
        const transmitterSelect = screen.getByRole('combobox', { name: 'Rig tracking transmitter' });
        expect(transmitterSelect).toHaveAttribute('aria-disabled', 'true');

        fireEvent.mouseDown(rigSelect);
        fireEvent.click(screen.getByRole('option', { name: 'Radio 1' }));
        expect(transmitterSelect).not.toHaveAttribute('aria-disabled', 'true');
        fireEvent.mouseDown(transmitterSelect);
        expect(screen.queryByRole('option', { name: /Uplink only/ })).not.toBeInTheDocument();
        fireEvent.click(screen.getByRole('option', { name: /Downlink/ }));
        expect(screen.getByTestId('rig-state')).toHaveTextContent('"transmitter_id":"tx-1"');

        fireEvent.mouseDown(rigSelect);
        fireEvent.click(screen.getByRole('option', { name: 'None' }));
        expect(screen.getByTestId('rig-state')).toHaveTextContent('"transmitter_id":""');
    });
});
