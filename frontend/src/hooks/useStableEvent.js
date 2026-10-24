import {useCallback, useLayoutEffect, useRef} from 'react';

// Memoized children can keep this callback without retaining an old render's state.
export default function useStableEvent(handler) {
    const handlerRef = useRef(handler);
    useLayoutEffect(() => {
        handlerRef.current = handler;
    }, [handler]);
    return useCallback((...args) => handlerRef.current(...args), []);
}
