import React from 'react';
import { fireEvent, render, screen, within } from '@testing-library/react';
import { expect, test, vi } from 'vitest';
import WaterfallErrorDialog from '../waterfall-error-dialog.jsx';

test.each([
    [
        'Airspy',
        'Error in native Airspy worker process: open Airspy device by serial 0000000000001234 failed: AIRSPY_ERROR_NOT_FOUND (code -5)',
    ],
    [
        'SoapySDR',
        'SoapySDR opening device failed (driver=rtlsdr, serial=1234): RuntimeError: Device busy',
    ],
    [
        'UHD',
        'UHD selecting device failed (serial=ABC123): LookupError: No matching USRP among 2 discovered device(s); available serials: XYZ, DEF',
    ],
    [
        'RTL-SDR',
        'RTL-SDR USB opening device failed (serial=00000001): LibUSBError: <LIBUSB_ERROR_BUSY (-6): Resource busy> "Could not open SDR"',
    ],
])('shows the full %s diagnostic below the failed operation', (_backend, nativeError) => {
    render(<WaterfallErrorDialog
        open
        message={`Failed to start SDR stream: ${nativeError}`}
        onClose={vi.fn()}
    />);

    const dialog = screen.getByRole('dialog');
    expect(within(dialog).getByRole('alert')).toHaveTextContent('Failed to start SDR stream');
    expect(dialog.querySelector('pre')).toHaveTextContent(nativeError);
});

test('preserves multiline native errors and accepts Error objects', () => {
    const onClose = vi.fn();
    render(<WaterfallErrorDialog
        open
        message={new Error('Failed to configure SDR: device=ABC123\nUSB access denied')}
        onClose={onClose}
    />);

    const dialog = screen.getByRole('dialog');
    expect(dialog.querySelector('pre').textContent).toBe('device=ABC123\nUSB access denied');
    fireEvent.click(within(dialog).getByRole('button'));
    expect(onClose).toHaveBeenCalledOnce();
});

test('shows a missing-setting prompt without an empty technical-details panel', () => {
    render(<WaterfallErrorDialog
        open
        message={'Select sample rate before starting stream\nSelect antenna before starting stream'}
        onClose={vi.fn()}
    />);

    const dialog = screen.getByRole('dialog');
    expect(within(dialog).getByRole('alert').textContent).toBe(
        'Select sample rate before starting stream\nSelect antenna before starting stream'
    );
    expect(dialog.querySelector('pre')).toBeNull();
});
