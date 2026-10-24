import {useEffect} from 'react';
import {act, render, renderHook, screen} from '@testing-library/react';
import {afterEach, describe, expect, it, vi} from 'vitest';
import {
    getMapErrorMessage,
    MapRendererErrorBoundary,
    sanitizeMapErrorMessage,
    useMapLoadFailure,
} from '../map-load-error.jsx';

describe('map load failure handling', () => {
    afterEach(() => {
        vi.useRealTimers();
    });

    it('reports a renderer that never finishes loading and clears on retry', () => {
        vi.useFakeTimers();
        const {result} = renderHook(() => useMapLoadFailure({
            engine: 'MapLibre',
            loadKey: 'maplibre:satellite',
            timeoutMs: 1000,
        }));

        act(() => vi.advanceTimersByTime(1000));
        expect(result.current.failure).toEqual({
            engine: 'MapLibre',
            message: 'MapLibre did not finish loading the selected basemap within 1 second.',
        });

        act(() => result.current.retry());
        expect(result.current.failure).toBeNull();
        expect(result.current.attempt).toBe(1);
    });

    it('does not report a timeout after the basemap has loaded', () => {
        vi.useFakeTimers();
        const {result} = renderHook(() => useMapLoadFailure({
            engine: 'Leaflet',
            loadKey: 'leaflet:osm',
            timeoutMs: 1000,
        }));

        act(() => result.current.reportLoaded());
        act(() => vi.advanceTimersByTime(1000));
        expect(result.current.failure).toBeNull();
    });

    it('redacts tile-provider credentials from technical errors', () => {
        expect(sanitizeMapErrorMessage(
            'Request failed: https://tiles.example/1/2/3.png?api_key=secret-value&style=dark'
        )).toBe(
            'Request failed: https://tiles.example/1/2/3.png?api_key=[redacted]&style=dark'
        );
        expect(getMapErrorMessage(
            {error: {message: 'Worker failed with access_token=another-secret'}},
            'Fallback'
        )).toBe('Worker failed with access_token=[redacted]');
    });

    it('keeps a healthy renderer mounted when its projection changes', () => {
        const onMount = vi.fn();
        const onUnmount = vi.fn();
        const Renderer = ({projection}) => {
            useEffect(() => {
                onMount();
                return onUnmount;
            }, []);
            return <div>{projection}</div>;
        };

        const {rerender, unmount} = render(
            <MapRendererErrorBoundary resetKey="mercator">
                <Renderer projection="mercator"/>
            </MapRendererErrorBoundary>
        );
        rerender(
            <MapRendererErrorBoundary resetKey="globe">
                <Renderer projection="globe"/>
            </MapRendererErrorBoundary>
        );

        expect(screen.getByText('globe')).toBeTruthy();
        expect(onMount).toHaveBeenCalledTimes(1);
        expect(onUnmount).not.toHaveBeenCalled();
        unmount();
        expect(onUnmount).toHaveBeenCalledTimes(1);
    });

    it('recovers a failed renderer when its projection changes', () => {
        const onError = vi.fn();
        const consoleError = vi.spyOn(console, 'error').mockImplementation(() => {});
        const Renderer = ({fail}) => {
            if (fail) throw new Error('renderer failed');
            return <div>map recovered</div>;
        };

        try {
            const {rerender} = render(
                <MapRendererErrorBoundary resetKey="mercator" onError={onError}>
                    <Renderer fail/>
                </MapRendererErrorBoundary>
            );
            expect(screen.queryByText('map recovered')).toBeNull();
            expect(onError).toHaveBeenCalledTimes(1);

            rerender(
                <MapRendererErrorBoundary resetKey="globe" onError={onError}>
                    <Renderer fail={false}/>
                </MapRendererErrorBoundary>
            );
            expect(screen.getByText('map recovered')).toBeTruthy();
            expect(onError).toHaveBeenCalledTimes(1);
        } finally {
            consoleError.mockRestore();
        }
    });
});
