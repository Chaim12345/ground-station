import React from 'react';
import { configureStore } from '@reduxjs/toolkit';
import { Provider } from 'react-redux';
import { render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import TranscriptionSubtitles from '../transcription-subtitles.jsx';
import transcriptionReducer, { addTranscription } from '../transcription-slice.jsx';

vi.mock('../../../hooks/useUserTimeSettings.jsx', () => ({
    useUserTimeSettings: () => ({ timezone: 'UTC', locale: 'en' }),
}));

const makeStore = (sourceLanguage = 'auto', translationBackend = 'google') => configureStore({
    reducer: {
        transcription: transcriptionReducer,
        vfo: () => ({
            vfoColors: ['#4477aa'],
            vfoMarkers: {
                1: {
                    transcriptionProvider: 'parakeet',
                    transcriptionLanguage: sourceLanguage,
                    transcriptionTranslateTo: 'en',
                    transcriptionTranslationBackend: translationBackend,
                },
            },
            streamingVFOs: [1],
            vfoMuted: { 1: false },
        }),
    },
});

describe('subtitle detected language', () => {
    it('shows the detected source when Auto is selected, including during translation', () => {
        const store = makeStore();
        store.dispatch(addTranscription({
            text: 'This is a translated caption.',
            sessionId: 'session',
            vfoNumber: 1,
            language: 'auto',
            detectedLanguage: 'ru',
            is_final: true,
        }));
        store.dispatch(addTranscription({
            text: 'Here is another caption.',
            sessionId: 'session',
            vfoNumber: 1,
            language: 'auto',
            detectedLanguage: 'ru',
            is_final: true,
        }));

        expect(store.getState().transcription.liveTranscription.session_1.detectedLanguage).toBe('ru');
        render(<Provider store={store}><TranscriptionSubtitles /></Provider>);

        expect(screen.getByText('ru')).toBeInTheDocument();
        expect(screen.getByText('en')).toBeInTheDocument();
        expect(screen.queryByText('AUTO')).not.toBeInTheDocument();
    });

    it('keeps a selected source language instead of showing a detected one', () => {
        const store = makeStore('el');
        store.dispatch(addTranscription({
            text: 'This is a translated caption.',
            sessionId: 'session',
            vfoNumber: 1,
            language: 'el',
            detectedLanguage: 'ru',
            is_final: true,
        }));

        render(<Provider store={store}><TranscriptionSubtitles /></Provider>);

        expect(screen.getByText('el')).toBeInTheDocument();
        expect(screen.queryByText('ru')).not.toBeInTheDocument();
    });

    it('shows detected language for an untranslated Parakeet caption', () => {
        const store = makeStore('auto', 'none');
        store.dispatch(addTranscription({
            text: 'Это русская радиопередача.',
            sessionId: 'session',
            vfoNumber: 1,
            language: 'auto',
            detectedLanguage: 'ru',
            is_final: true,
        }));

        render(<Provider store={store}><TranscriptionSubtitles /></Provider>);

        expect(screen.getByText('ru')).toBeInTheDocument();
        expect(screen.queryByText('en')).not.toBeInTheDocument();
    });

    it('clears the old label when a new worker has not detected a language', () => {
        const store = makeStore();
        store.dispatch(addTranscription({
            text: 'Old caption', sessionId: 'session', vfoNumber: 1,
            language: 'auto', detectedLanguage: 'ru', is_final: true,
        }));
        store.dispatch(addTranscription({
            text: 'New caption', sessionId: 'session', vfoNumber: 1,
            language: 'auto', is_final: true,
        }));

        expect(store.getState().transcription.liveTranscription.session_1.detectedLanguage).toBeNull();
    });
});
