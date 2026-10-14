import React from 'react';
import { configureStore } from '@reduxjs/toolkit';
import { Provider } from 'react-redux';
import { act, fireEvent, render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import TranscriptionSubtitles from '../transcription-subtitles.jsx';
import transcriptionReducer, { addTranscription } from '../transcription-slice.jsx';

vi.mock('../../../hooks/useUserTimeSettings.jsx', () => ({
    useUserTimeSettings: () => ({ timezone: 'UTC', locale: 'en' }),
}));

const makeStore = () => configureStore({
    reducer: {
        transcription: transcriptionReducer,
        vfo: () => ({
            vfoColors: ['#4477aa'],
            vfoMarkers: { 1: {} },
            streamingVFOs: [1],
            vfoMuted: { 1: false },
        }),
    },
});

const appendCaption = (store, text) => {
    act(() => {
        store.dispatch(addTranscription({
            text,
            sessionId: 'session',
            vfoNumber: 1,
            language: 'en',
            is_final: true,
        }));
    });
};

describe('subtitle scrolling', () => {
    it('follows incoming text until the user scrolls up, then resumes at the bottom', () => {
        const store = makeStore();
        appendCaption(store, 'first caption');
        render(<Provider store={store}><TranscriptionSubtitles maxWordsPerLine={2} /></Provider>);

        const scroller = screen.getByTestId('transcription-scroll-vfo1');
        let scrollHeight = 200;
        Object.defineProperties(scroller, {
            scrollHeight: { get: () => scrollHeight },
            clientHeight: { get: () => 100 },
        });

        scroller.scrollTop = 100;
        fireEvent.scroll(scroller);
        scrollHeight = 320;
        appendCaption(store, 'second caption');
        expect(scroller.scrollTop).toBe(320);

        scroller.scrollTop = 215; // Five pixels above the real bottom unlocks following.
        fireEvent.scroll(scroller);
        scrollHeight = 450;
        appendCaption(store, 'third caption');
        expect(scroller.scrollTop).toBe(215);

        scroller.scrollTop = 350;
        fireEvent.scroll(scroller);
        scrollHeight = 550;
        appendCaption(store, 'fourth caption');
        expect(scroller.scrollTop).toBe(550);
    });
});
