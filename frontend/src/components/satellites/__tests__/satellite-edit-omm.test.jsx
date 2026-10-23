import React from 'react';
import {render, screen} from '@testing-library/react';
import {configureStore} from '@reduxjs/toolkit';
import {Provider} from 'react-redux';
import {describe, expect, it, vi} from 'vitest';
import '../../../i18n/config.js';
import SatelliteEditDialog from '../satellite-edit-dialog.jsx';

vi.mock('../../common/socket.jsx', () => ({useSocket: () => ({socket: {emit: vi.fn()}})}));

const renderEditor = (orbitModelKind, orbitPayload) => {
    const store = configureStore({reducer: {satellites: () => ({})}});
    render(
        <Provider store={store}>
            <SatelliteEditDialog
                open
                onClose={vi.fn()}
                satelliteData={{
                    norad_id: 100000,
                    name: 'OMM-only satellite',
                    orbit_model_kind: orbitModelKind,
                    orbit_payload: orbitPayload,
                    tle1: '',
                    tle2: '',
                }}
            />
        </Provider>
    );
    return screen.getByRole('button', {name: 'Edit'});
};

describe('satellite orbit editor validation', () => {
    it('allows an OMM-only satellite to be saved without TLE lines', () => {
        expect(renderEditor('omm', {EPOCH: '2025-03-26T05:19:34Z'})).toBeEnabled();
    });

    it('still requires TLE lines for TLE models and an OMM payload for OMM models', () => {
        expect(renderEditor('tle', null)).toBeDisabled();
    });

    it('rejects an OMM model with no payload', () => {
        expect(renderEditor('omm', null)).toBeDisabled();
    });
});
