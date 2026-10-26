import React from 'react';
import { Box } from '@mui/material';
import RadarPage from './radar-page.jsx';

// Simple layout wrapper so the radar page slots into the dashboard route
// layout the same way the scheduler page does.
export default function RadarLayout() {
    return (
        <Box
            sx={{
                height: '100%',
                width: '100%',
                boxSizing: 'border-box',
                minWidth: 0,
                display: 'flex',
                flexDirection: 'column',
                p: 2,
                gap: 2,
                overflow: 'auto',
                position: 'relative',
            }}
        >
            <RadarPage />
        </Box>
    );
}
