import React from 'react';
import { Box, CircularProgress, Typography } from '@mui/material';
import { useTranslation } from 'react-i18next';

export default function WaterfallGenerationOverlay() {
    const { t } = useTranslation('filebrowser');
    const label = t('filebrowser_main.generating_waterfall', { defaultValue: 'Generating waterfall' });

    return (
        <Box
            role="status"
            aria-label={label}
            sx={{
                position: 'absolute',
                inset: 0,
                zIndex: 1,
                display: 'flex',
                flexDirection: 'column',
                alignItems: 'center',
                justifyContent: 'center',
                gap: 1,
                bgcolor: 'rgba(0, 0, 0, 0.58)',
                color: 'common.white',
                pointerEvents: 'none',
            }}
        >
            <CircularProgress size={32} color="inherit" />
            <Typography variant="caption" sx={{ fontWeight: 700 }}>
                {label}
            </Typography>
        </Box>
    );
}
