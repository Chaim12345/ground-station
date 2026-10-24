import React from 'react';
import { configureStore } from '@reduxjs/toolkit';
import { Provider } from 'react-redux';
import { act, renderHook } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import { useVfoAudioState, useVfoDecoderInfo } from './vfo-hooks.js';

const setVfoMute = vi.hoisted(() => vi.fn());

vi.mock('../../dashboard/audio-provider.jsx', () => ({
    useAudio: () => ({ setVfoMute }),
}));

describe('VFO callback props', () => {
    it('stays stable on unrelated renders and updates when mute or decoder data changes', () => {
        const firstDecoder = { id: 'decoder-1' };
        const nextDecoder = { id: 'decoder-2' };
        const initialState = {
            vfo: { vfoMuted: { 1: false } },
            decoders: { active: { session1_vfo1: firstDecoder }, currentSessionId: 'session1' },
        };
        const store = configureStore({
            reducer: (state = initialState, action) => action.type === 'replace' ? action.payload : state,
        });
        const wrapper = ({ children }) => <Provider store={store}>{children}</Provider>;
        const { result, rerender } = renderHook(() => ({
            audio: useVfoAudioState(),
            decoder: useVfoDecoderInfo(),
        }), { wrapper });

        const initialMuteToggle = result.current.audio.handleVfoMuteToggle;
        const initialDecoderLookup = result.current.decoder.getVFODecoderInfo;
        rerender();
        expect(result.current.audio.handleVfoMuteToggle).toBe(initialMuteToggle);
        expect(result.current.decoder.getVFODecoderInfo).toBe(initialDecoderLookup);
        expect(initialDecoderLookup(1)).toBe(firstDecoder);

        act(() => store.dispatch({
            type: 'replace',
            payload: { ...initialState, vfo: { vfoMuted: { 1: true } } },
        }));
        expect(result.current.audio.handleVfoMuteToggle).not.toBe(initialMuteToggle);
        expect(result.current.decoder.getVFODecoderInfo).toBe(initialDecoderLookup);
        result.current.audio.handleVfoMuteToggle(1);
        expect(setVfoMute).toHaveBeenCalledExactlyOnceWith(1, false);

        act(() => store.dispatch({
            type: 'replace',
            payload: {
                vfo: { vfoMuted: { 1: true } },
                decoders: { active: { session2_vfo1: nextDecoder }, currentSessionId: 'session2' },
            },
        }));
        expect(result.current.decoder.getVFODecoderInfo).not.toBe(initialDecoderLookup);
        expect(result.current.decoder.getVFODecoderInfo(1)).toBe(nextDecoder);
        expect(result.current.decoder.getVFODecoderInfo(2)).toBeNull();
    });
});
