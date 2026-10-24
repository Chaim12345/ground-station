import {renderHook} from '@testing-library/react';
import {describe, expect, it} from 'vitest';
import useStableEvent from './useStableEvent.js';

describe('useStableEvent', () => {
    it('keeps event identity while reading the latest committed state', () => {
        const {result, rerender} = renderHook(
            ({frequency}) => useStableEvent((offset) => frequency + offset),
            {initialProps: {frequency: 100}},
        );
        const onTune = result.current;

        rerender({frequency: 200});

        expect(result.current).toBe(onTune);
        expect(onTune(5)).toBe(205);
    });
});
