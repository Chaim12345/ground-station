import React from 'react';
import { Box } from '@mui/material';
import { parseTargetSlotNumber } from '../target/celestial-target-utils.js';
import TargetNumberIcon from '../common/target-number-icon.jsx';

const TransmitterTargetLabel = ({ transmitter, sx = {} }) => {
    const targetNumber = parseTargetSlotNumber(transmitter.trackerId);
    const targetName = transmitter.trackerTargetName || '';

    return (
        <Box sx={{ display: 'inline-flex', alignItems: 'center', gap: 0.65, minWidth: 0, maxWidth: '100%', ...sx }}>
            {targetNumber != null ? (
                <TargetNumberIcon targetNumber={targetNumber} size={14} sx={{ flexShrink: 0 }} />
            ) : transmitter.trackerLabel ? (
                <Box component="span" sx={{ color: 'text.secondary', fontWeight: 700, flexShrink: 0 }}>
                    {transmitter.trackerLabel}
                </Box>
            ) : null}
            {targetName && (
                <Box
                    component="span"
                    title={targetName}
                    sx={{ color: 'text.secondary', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}
                >
                    {targetName}
                </Box>
            )}
        </Box>
    );
};

export default TransmitterTargetLabel;
