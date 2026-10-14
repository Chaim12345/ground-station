import React from 'react';
import { describe, expect, it, vi } from 'vitest';
import { render, screen } from '@testing-library/react';

vi.mock('react-i18next', () => ({
    useTranslation: () => ({ t: (_key, options) => options?.defaultValue || _key }),
}));

import { getLibreTargetOptions, isLibrePairSupported, resolveTranslationBackend, TranslationBackendField } from '../translation-options.jsx';

const capabilities = {
    configured: true,
    languages: [
        { code: 'en', name: 'English', targets: ['el', 'zh-Hans'] },
        { code: 'el', name: 'Greek', targets: ['en'] },
        { code: 'zh-Hans', name: 'Chinese (Simplified)', targets: ['en'] },
        { code: 'zh-Hant', name: 'Chinese (Traditional)', targets: ['en'] },
    ],
};

describe('LibreTranslate language choices', () => {
    it('keeps Google for older tasks with a target but no service field', () => {
        expect(resolveTranslationBackend(undefined, 'el')).toBe('google');
        expect(resolveTranslationBackend(undefined, 'none')).toBe('none');
        expect(resolveTranslationBackend('none', 'el')).toBe('none');
    });

    it('maps legacy Chinese to Simplified and checks each source/target pair', () => {
        expect(isLibrePairSupported(capabilities, 'en', 'zh')).toBe(true);
        expect(isLibrePairSupported(capabilities, 'auto', 'zh')).toBe(true);
        expect(isLibrePairSupported(capabilities, 'el', 'zh')).toBe(false);
        expect(isLibrePairSupported(capabilities, 'hr', 'en')).toBe(false);
        expect(isLibrePairSupported(null, 'en', 'el')).toBe(false);
    });

    it('offers Traditional Chinese without duplicating the legacy Chinese option', () => {
        const options = getLibreTargetOptions([
            { value: 'none' }, { value: 'zh' }, { value: 'en' },
        ], capabilities);
        expect(options.filter((option) => option.value === 'zh')).toHaveLength(1);
        expect(options.some((option) => option.value === 'zh-Hans')).toBe(false);
        expect(options.some((option) => option.value === 'zh-Hant')).toBe(true);
    });

    it('warns when a selected pair is unavailable without changing the selection', () => {
        render(<TranslationBackendField value="libretranslate" onChange={vi.fn()} source="el" target="zh" capabilities={capabilities} />);
        expect(screen.getByText('This language pair is not supported by the configured LibreTranslate service.')).toBeInTheDocument();
    });
});
