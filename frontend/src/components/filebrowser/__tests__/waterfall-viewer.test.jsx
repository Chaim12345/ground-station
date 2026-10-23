import { afterEach, describe, expect, it, vi } from 'vitest';
import { act, fireEvent, screen, within } from '@testing-library/react';
import { renderWithProviders } from '../../../test/test-utils.jsx';
import WaterfallViewer from '../waterfall-viewer.jsx';

vi.mock('../../waterfall/frequency-scale.jsx', () => ({
    default: ({ interactionActive = false, allowInteractionMeasure = false }) => (
        <div
            data-testid="frequency-scale"
            data-interaction-active={String(interactionActive)}
            data-allow-interaction-measure={String(allowInteractionMeasure)}
        />
    ),
}));

const renderViewer = (props = {}) => renderWithProviders(
    <WaterfallViewer
        src="/waterfall.png"
        alt="Recorded waterfall"
        centerFrequency={145_800_000}
        sampleRate={2_400_000}
        {...props}
    />
);

const mockViewerRect = () => vi.spyOn(HTMLElement.prototype, 'getBoundingClientRect').mockReturnValue({
    width: 400,
    height: 240,
    top: 0,
    left: 0,
    right: 400,
    bottom: 240,
    x: 0,
    y: 0,
    toJSON: () => {},
});

const stubPointerCapture = (viewer) => {
    viewer.setPointerCapture = vi.fn();
    viewer.hasPointerCapture = vi.fn(() => false);
    viewer.releasePointerCapture = vi.fn();
};

afterEach(() => {
    vi.useRealTimers();
    vi.restoreAllMocks();
});

describe('WaterfallViewer drag handling', () => {
    it('opens a grouped transmitter list on click without a hover tooltip', () => {
        mockViewerRect();
        renderViewer({ transmitters: Array.from({ length: 8 }, (_, index) => ({
            id: `tx-${index}`,
            description: `Transmitter ${index + 1}`,
            downlink_low: 145_800_000,
        })) });

        const label = screen.getByTestId('waterfall-transmitter-label');
        expect(screen.getAllByTestId('waterfall-transmitter-marker')).toHaveLength(1);
        fireEvent.mouseEnter(label);
        expect(screen.getAllByTestId('waterfall-transmitter-search-band')).toHaveLength(1);
        expect(screen.queryByRole('tooltip')).not.toBeInTheDocument();

        fireEvent.click(label);
        const details = screen.getByTestId('waterfall-transmitter-details');
        expect(within(details).getByText('Transmitter 8')).toBeInTheDocument();
        expect(within(details).getAllByTestId('waterfall-transmitter-frequency-group')).toHaveLength(1);
        expect(within(details).getAllByText('Estimated search area')).toHaveLength(1);
        expect(screen.getAllByTestId('waterfall-transmitter-search-band')).toHaveLength(1);
        expect(screen.queryByRole('tooltip')).not.toBeInTheDocument();

        fireEvent.keyDown(details, { key: 'Escape' });
        expect(screen.queryByTestId('waterfall-transmitter-details')).not.toBeInTheDocument();

        fireEvent.keyDown(label, { key: 'Enter' });
        expect(screen.getByTestId('waterfall-transmitter-details')).toBeInTheDocument();

        fireEvent.click(screen.getByRole('button', { name: 'Close transmitter details' }));
        expect(screen.queryByTestId('waterfall-transmitter-details')).not.toBeInTheDocument();

        fireEvent.click(label);
        expect(screen.getByTestId('waterfall-transmitter-details')).toBeInTheDocument();
        fireEvent.click(label);
        expect(screen.queryByTestId('waterfall-transmitter-details')).not.toBeInTheDocument();

        fireEvent.click(label);
        const viewer = screen.getByTestId('waterfall-viewer');
        stubPointerCapture(viewer);
        const backdrop = document.querySelector('.MuiPopover-root .MuiBackdrop-root');
        expect(backdrop).toBeInTheDocument();
        fireEvent.pointerDown(backdrop, { pointerType: 'mouse', pointerId: 5, button: 0 });
        expect(viewer.setPointerCapture).not.toHaveBeenCalled();
        fireEvent.click(backdrop);
        expect(screen.queryByTestId('waterfall-transmitter-details')).not.toBeInTheDocument();
    });

    it('shows an estimated search band on transmitter hover and keeps it aligned through zoom', () => {
        mockViewerRect();
        renderViewer({ transmitters: [
            { id: 'beacon', description: 'Beacon', downlink_low: 145_800_000 },
        ] });

        const label = screen.getByTestId('waterfall-transmitter-label');
        expect(screen.queryByTestId('waterfall-transmitter-search-band')).not.toBeInTheDocument();
        fireEvent.mouseEnter(label);

        const searchBand = screen.getByTestId('waterfall-transmitter-search-band');
        expect(searchBand).toHaveAttribute('data-frequency-low', '145781109');
        expect(searchBand).toHaveAttribute('data-frequency-high', '145818891');
        expect(screen.getByTestId('waterfall-transmitter-signal-band')).toBeInTheDocument();
        expect(screen.queryByRole('tooltip')).not.toBeInTheDocument();

        const initialWidth = Number.parseFloat(searchBand.style.width);
        fireEvent.click(screen.getByLabelText('Zoom In X'));
        expect(Number.parseFloat(searchBand.style.width)).toBeGreaterThan(initialWidth);

        fireEvent.mouseLeave(label);
        expect(screen.queryByTestId('waterfall-transmitter-search-band')).not.toBeInTheDocument();
    });

    it('opens details for a single transmitter from its label or arrow', () => {
        mockViewerRect();
        renderViewer({ transmitters: [
            { id: 'beacon', description: 'Beacon', downlink_low: 145_800_000 },
        ] });

        const label = screen.getByTestId('waterfall-transmitter-label');
        fireEvent.mouseEnter(label);
        expect(screen.queryByRole('tooltip')).not.toBeInTheDocument();
        fireEvent.click(label);
        const details = screen.getByTestId('waterfall-transmitter-details');
        expect(within(details).getByText('Beacon')).toBeInTheDocument();
        expect(within(details).getByText('Estimated search area')).toBeInTheDocument();
        fireEvent.click(screen.getByRole('button', { name: 'Close transmitter details' }));

        const marker = screen.getByTestId('waterfall-transmitter-marker');
        fireEvent.mouseEnter(marker);
        expect(screen.queryByRole('tooltip')).not.toBeInTheDocument();
        fireEvent.keyDown(marker, { key: 'Enter' });
        expect(screen.getByTestId('waterfall-transmitter-details')).toBeInTheDocument();
    });

    it('highlights all frequencies in a grouped label and one when hovering its arrow', () => {
        mockViewerRect();
        renderViewer({ transmitters: [
            { id: 'beacon', description: 'Beacon', downlink_low: 145_800_000 },
            { id: 'telemetry', description: 'Telemetry', downlink_low: 146_400_000 },
        ] });

        fireEvent.mouseEnter(screen.getByText('Beacon +1'));
        expect(screen.getAllByTestId('waterfall-transmitter-search-band')).toHaveLength(2);
        expect(screen.queryByRole('tooltip')).not.toBeInTheDocument();

        fireEvent.click(screen.getByText('Beacon +1'));
        const details = screen.getByTestId('waterfall-transmitter-details');
        expect(within(details).getAllByTestId('waterfall-transmitter-frequency-group')).toHaveLength(2);
        expect(within(details).getByText('Beacon')).toBeInTheDocument();
        expect(within(details).getByText('Telemetry')).toBeInTheDocument();
        fireEvent.click(screen.getByRole('button', { name: 'Close transmitter details' }));

        const telemetry = screen.getAllByTestId('waterfall-transmitter-marker')
            .find((marker) => marker.dataset.frequency === '146400000');
        fireEvent.mouseEnter(telemetry);
        expect(screen.getAllByTestId('waterfall-transmitter-search-band')).toHaveLength(1);
        fireEvent.mouseLeave(telemetry);
        expect(screen.queryByTestId('waterfall-transmitter-search-band')).not.toBeInTheDocument();
    });

    it('aligns target transmitter arrows with the frequency scale through X zoom', () => {
        mockViewerRect();
        renderViewer({ transmitters: [
            { id: 'center', description: 'Beacon', downlink_low: 145_800_000 },
            { id: 'upper', description: 'Telemetry', downlink_low: 146_400_000 },
            { id: 'outside', description: 'Outside band', downlink_low: 149_000_000 },
        ] });

        expect(screen.getByTestId('waterfall-transmitter-strip')).toBeInTheDocument();
        expect(screen.getAllByTestId('waterfall-transmitter-marker')).toHaveLength(2);
        expect(screen.getByText('Beacon +1')).toBeInTheDocument();
        const telemetry = screen.getAllByTestId('waterfall-transmitter-marker')
            .find((marker) => marker.dataset.frequency === '146400000');
        expect(telemetry.style.left).toBe('300px');

        fireEvent.click(screen.getByLabelText('Zoom In X'));
        expect(telemetry.style.left).toBe('350px');
        expect(screen.getByText('Telemetry')).toBeInTheDocument();
    });

    it('leaves the transmitter strip hidden without downlinks in the recorded band', () => {
        mockViewerRect();
        renderViewer({ transmitters: [{ description: 'Uplink only', uplink_low: 145_800_000 }] });
        expect(screen.queryByTestId('waterfall-transmitter-strip')).not.toBeInTheDocument();
    });

    it('shows the visible part of a downlink range whose midpoint lies outside the recording', () => {
        mockViewerRect();
        renderViewer({ transmitters: [
            { description: 'Wide transponder', downlink_low: 146_900_000, downlink_high: 147_200_000 },
        ] });

        expect(screen.getByText('Wide transponder')).toBeInTheDocument();
        expect(screen.getByTestId('waterfall-transmitter-range')).toBeInTheDocument();
        expect(screen.getByTestId('waterfall-transmitter-marker')).toHaveAttribute('data-frequency', '146950000');

        fireEvent.mouseEnter(screen.getByText('Wide transponder'));
        const searchBand = screen.getByTestId('waterfall-transmitter-search-band');
        expect(Number(searchBand.dataset.frequencyLow)).toBeLessThan(146_900_000);
        expect(Number(searchBand.dataset.frequencyHigh)).toBeGreaterThan(147_200_000);
        expect(Number.parseFloat(searchBand.style.width)).toBeGreaterThan(
            Number.parseFloat(screen.getByTestId('waterfall-transmitter-signal-band').style.width)
        );
    });

    it('shows a loading indicator until the snapshot image loads', () => {
        renderViewer();
        expect(screen.getByRole('progressbar')).toBeInTheDocument();

        fireEvent.load(screen.getByAltText('Recorded waterfall'));
        expect(screen.queryByRole('progressbar')).not.toBeInTheDocument();
    });

    it('cancels native drag events from the rendered waterfall image', () => {
        renderViewer();

        const image = screen.getByAltText('Recorded waterfall');

        expect(fireEvent.dragStart(image)).toBe(false);
    });

    it('prevents the browser default on mouse pointer down inside the viewer', () => {
        const setPointerCapture = vi.fn();
        const hasPointerCapture = vi.fn(() => false);
        const viewer = renderViewer().getByTestId('waterfall-viewer');

        viewer.setPointerCapture = setPointerCapture;
        viewer.hasPointerCapture = hasPointerCapture;

        const pointerDown = new PointerEvent('pointerdown', {
            bubbles: true,
            cancelable: true,
            pointerId: 1,
            pointerType: 'mouse',
            button: 0,
            clientX: 120,
            clientY: 80,
        });

        viewer.dispatchEvent(pointerDown);

        expect(pointerDown.defaultPrevented).toBe(true);
        expect(setPointerCapture).toHaveBeenCalledWith(1);
    });

    it('places the crosshair on click and leaves it fixed through move and leave', () => {
        mockViewerRect();
        const viewer = renderViewer().getByTestId('waterfall-viewer');
        stubPointerCapture(viewer);

        act(() => {
            viewer.dispatchEvent(new PointerEvent('pointerdown', {
                bubbles: true,
                cancelable: true,
                pointerId: 1,
                pointerType: 'mouse',
                button: 0,
                clientX: 200,
                clientY: 120,
            }));
            viewer.dispatchEvent(new PointerEvent('pointerup', {
                bubbles: true,
                cancelable: true,
                pointerId: 1,
                pointerType: 'mouse',
                button: 0,
                clientX: 200,
                clientY: 120,
            }));
        });

        expect(screen.getByText('145.800000 MHz')).toBeInTheDocument();

        act(() => {
            viewer.dispatchEvent(new PointerEvent('pointermove', {
                bubbles: true,
                cancelable: true,
                pointerId: 1,
                pointerType: 'mouse',
                clientX: 100,
                clientY: 120,
            }));
            viewer.dispatchEvent(new PointerEvent('pointerleave', {
                bubbles: true,
                cancelable: true,
                pointerId: 1,
                pointerType: 'mouse',
                clientX: 100,
                clientY: 120,
            }));
        });

        expect(screen.getByText('145.800000 MHz')).toBeInTheDocument();
        expect(screen.queryByText('145.200000 MHz')).not.toBeInTheDocument();
    });

    it('repositions the crosshair on another click', () => {
        mockViewerRect();
        const viewer = renderViewer().getByTestId('waterfall-viewer');
        stubPointerCapture(viewer);

        act(() => {
            viewer.dispatchEvent(new PointerEvent('pointerdown', {
                bubbles: true,
                cancelable: true,
                pointerId: 1,
                pointerType: 'mouse',
                button: 0,
                clientX: 200,
                clientY: 120,
            }));
            viewer.dispatchEvent(new PointerEvent('pointerup', {
                bubbles: true,
                cancelable: true,
                pointerId: 1,
                pointerType: 'mouse',
                button: 0,
                clientX: 200,
                clientY: 120,
            }));
            viewer.dispatchEvent(new PointerEvent('pointerdown', {
                bubbles: true,
                cancelable: true,
                pointerId: 2,
                pointerType: 'mouse',
                button: 0,
                clientX: 100,
                clientY: 120,
            }));
            viewer.dispatchEvent(new PointerEvent('pointerup', {
                bubbles: true,
                cancelable: true,
                pointerId: 2,
                pointerType: 'mouse',
                button: 0,
                clientX: 100,
                clientY: 120,
            }));
        });

        expect(screen.getByText('145.200000 MHz')).toBeInTheDocument();
        expect(screen.queryByText('145.800000 MHz')).not.toBeInTheDocument();
    });

    it('keeps the crosshair anchored to the same frequency while zooming', () => {
        mockViewerRect();
        const viewer = renderViewer().getByTestId('waterfall-viewer');
        stubPointerCapture(viewer);

        act(() => {
            viewer.dispatchEvent(new PointerEvent('pointerdown', {
                bubbles: true,
                cancelable: true,
                pointerId: 1,
                pointerType: 'mouse',
                button: 0,
                clientX: 100,
                clientY: 120,
            }));
            viewer.dispatchEvent(new PointerEvent('pointerup', {
                bubbles: true,
                cancelable: true,
                pointerId: 1,
                pointerType: 'mouse',
                button: 0,
                clientX: 100,
                clientY: 120,
            }));
        });

        const crosshair = screen.getByTestId('waterfall-crosshair-vertical');
        expect(crosshair.style.left).toBe('100px');
        expect(screen.getByText('145.200000 MHz')).toBeInTheDocument();

        fireEvent.click(screen.getByLabelText('Zoom In X'));

        expect(crosshair.style.left).toBe('50px');
        expect(screen.getByText('145.200000 MHz')).toBeInTheDocument();
    });

    it('places the crosshair on a touch tap', () => {
        mockViewerRect();
        const viewer = renderViewer().getByTestId('waterfall-viewer');
        stubPointerCapture(viewer);

        act(() => {
            viewer.dispatchEvent(new PointerEvent('pointerdown', {
                bubbles: true,
                cancelable: true,
                pointerId: 1,
                pointerType: 'touch',
                clientX: 200,
                clientY: 120,
            }));
            viewer.dispatchEvent(new PointerEvent('pointerup', {
                bubbles: true,
                cancelable: true,
                pointerId: 1,
                pointerType: 'touch',
                clientX: 200,
                clientY: 120,
            }));
        });

        expect(screen.getByText('145.800000 MHz')).toBeInTheDocument();
    });

    it('keeps touch movement from showing the mouse crosshair', () => {
        mockViewerRect();
        const viewer = renderViewer().getByTestId('waterfall-viewer');
        stubPointerCapture(viewer);

        act(() => {
            viewer.dispatchEvent(new PointerEvent('pointerdown', {
                bubbles: true,
                cancelable: true,
                pointerId: 1,
                pointerType: 'touch',
                clientX: 120,
                clientY: 90,
            }));
            viewer.dispatchEvent(new PointerEvent('pointermove', {
                bubbles: true,
                cancelable: true,
                pointerId: 1,
                pointerType: 'touch',
                clientX: 150,
                clientY: 95,
            }));
        });

        expect(screen.queryByText('145.500000 MHz')).not.toBeInTheDocument();
    });

    it('zooms the X axis with a two-finger touch pinch', () => {
        mockViewerRect();
        const viewer = renderViewer().getByTestId('waterfall-viewer');
        stubPointerCapture(viewer);

        act(() => {
            viewer.dispatchEvent(new PointerEvent('pointerdown', {
                bubbles: true,
                cancelable: true,
                pointerId: 1,
                pointerType: 'touch',
                clientX: 100,
                clientY: 100,
            }));
            viewer.dispatchEvent(new PointerEvent('pointerdown', {
                bubbles: true,
                cancelable: true,
                pointerId: 2,
                pointerType: 'touch',
                clientX: 200,
                clientY: 100,
            }));
            viewer.dispatchEvent(new PointerEvent('pointermove', {
                bubbles: true,
                cancelable: true,
                pointerId: 1,
                pointerType: 'touch',
                clientX: 50,
                clientY: 100,
            }));
            viewer.dispatchEvent(new PointerEvent('pointermove', {
                bubbles: true,
                cancelable: true,
                pointerId: 2,
                pointerType: 'touch',
                clientX: 250,
                clientY: 100,
            }));
        });

        expect(screen.getByText('Zoom 2.0x')).toBeInTheDocument();
    });

    it('pauses frequency scale measurement while a touch pinch transform is active', () => {
        vi.useFakeTimers();
        mockViewerRect();
        const viewer = renderViewer().getByTestId('waterfall-viewer');
        stubPointerCapture(viewer);

        expect(screen.getByTestId('frequency-scale')).toHaveAttribute('data-interaction-active', 'false');

        act(() => {
            viewer.dispatchEvent(new PointerEvent('pointerdown', {
                bubbles: true,
                cancelable: true,
                pointerId: 1,
                pointerType: 'touch',
                clientX: 100,
                clientY: 100,
            }));
            viewer.dispatchEvent(new PointerEvent('pointerdown', {
                bubbles: true,
                cancelable: true,
                pointerId: 2,
                pointerType: 'touch',
                clientX: 200,
                clientY: 100,
            }));
            viewer.dispatchEvent(new PointerEvent('pointermove', {
                bubbles: true,
                cancelable: true,
                pointerId: 1,
                pointerType: 'touch',
                clientX: 50,
                clientY: 100,
            }));
        });

        expect(screen.getByTestId('frequency-scale')).toHaveAttribute('data-interaction-active', 'true');
        expect(screen.getByTestId('frequency-scale')).toHaveAttribute('data-allow-interaction-measure', 'false');

        act(() => {
            vi.advanceTimersByTime(130);
        });

        expect(screen.getByTestId('frequency-scale')).toHaveAttribute('data-interaction-active', 'false');
    });
});
