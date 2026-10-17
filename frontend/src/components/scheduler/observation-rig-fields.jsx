/**
 * @license
 * Copyright (c) 2026 Efstratios Goudelis
 *
 * This program is free software: you can redistribute it and/or modify
 * it under the terms of the GNU General Public License as published by
 * the Free Software Foundation, either version 3 of the License, or
 * (at your option) any later version.
 */

import React, { useId } from 'react';
import { Box, FormControl, InputLabel, MenuItem, Select, Stack, Typography } from '@mui/material';

export function ObservationRigFields({ rig, rigs, transmitters, disabled, onChange, t }) {
    const rigLabelId = useId();
    const transmitterLabelId = useId();
    const available = transmitters.filter((transmitter) => Number(transmitter.downlink_low) > 0);
    const selectedTransmitter = available.some((transmitter) => String(transmitter.id) === String(rig?.transmitter_id))
        ? String(rig.transmitter_id)
        : '';

    return (
        <Box>
            <Typography variant="subtitle2" gutterBottom sx={{ color: 'primary.main', fontWeight: 'bold' }}>
                {t('scheduler_dialogs.shared.observation_rig')}
            </Typography>
            <Stack spacing={2}>
                <FormControl fullWidth size="small" disabled={disabled}>
                    <InputLabel id={rigLabelId}>{t('scheduler_dialogs.shared.observation_rig')}</InputLabel>
                    <Select
                        labelId={rigLabelId}
                        value={rig?.id || '__none__'}
                        label={t('scheduler_dialogs.shared.observation_rig')}
                        onChange={(event) => onChange({
                            ...rig,
                            id: event.target.value === '__none__' ? null : event.target.value,
                            transmitter_id: event.target.value === '__none__' ? '' : rig?.transmitter_id || '',
                            doppler_correction: event.target.value !== '__none__',
                        })}
                    >
                        <MenuItem value="__none__"><em>{t('scheduler_dialogs.shared.none')}</em></MenuItem>
                        {rigs.map((item) => (
                            <MenuItem key={item.id} value={item.id}>{item.name || item.id}</MenuItem>
                        ))}
                    </Select>
                </FormControl>
                <FormControl fullWidth size="small" required disabled={disabled || !rig?.id || !available.length}>
                    <InputLabel id={transmitterLabelId}>{t('scheduler_dialogs.shared.observation_rig_transmitter')}</InputLabel>
                    <Select
                        labelId={transmitterLabelId}
                        value={selectedTransmitter}
                        label={t('scheduler_dialogs.shared.observation_rig_transmitter')}
                        onChange={(event) => onChange({ ...rig, transmitter_id: event.target.value })}
                    >
                        {available.map((transmitter) => (
                            <MenuItem key={transmitter.id} value={String(transmitter.id)}>
                                {transmitter.description || transmitter.id} — {(Number(transmitter.downlink_low) / 1000000).toFixed(3)} MHz
                            </MenuItem>
                        ))}
                    </Select>
                </FormControl>
            </Stack>
        </Box>
    );
}
