import { configureStore } from '@reduxjs/toolkit';
import { expect, test, vi } from 'vitest';
import reducer, {
    deleteLibraryItems,
    fetchFiles,
    fetchPurgePreview,
    startLibraryPurge,
    purgeProgressUpdated,
    purgeFinished,
    clearPurgeState,
    setPageSize,
    setPage,
    setAllFilters,
    toggleFilter,
    reconcileFileBrowserPersistedState,
} from '../filebrowser-slice.jsx';

function createStore() {
    return configureStore({ reducer: { filebrowser: reducer } });
}

test('rehydrates the new observation filter from the former decoded choice', () => {
    const current = reducer(undefined, { type: '@@INIT' });
    const restored = reconcileFileBrowserPersistedState({
        filters: { showDecoded: false, showAudio: true },
    }, current);

    expect(restored.filters).toMatchObject({ showDecoded: false, showObservations: false });
    const changed = reducer(restored, toggleFilter('showObservations'));
    expect(changed.filters).toMatchObject({ showDecoded: false, showObservations: true });
    expect(changed.page).toBe(1);
});

test('selecting or clearing every type updates filters in one action', () => {
    const store = createStore();
    store.dispatch(setPage(3));
    store.dispatch(setAllFilters(false));

    expect(Object.values(store.getState().filebrowser.filters).every((enabled) => !enabled)).toBe(true);
    expect(store.getState().filebrowser.page).toBe(1);

    store.dispatch(setAllFilters(true));
    expect(Object.values(store.getState().filebrowser.filters).every(Boolean)).toBe(true);
});

test('queries one server-side page and stores its total', async () => {
    const socket = {
        connected: true,
        emit: vi.fn((event, payload, callback) => {
            expect(event).toBe('api.call');
            expect(payload).toEqual({
                cmd: 'filebrowser.query',
                data: {
                    filters: { showAudio: true },
                    sortBy: 'modified',
                    sortOrder: 'desc',
                    page: 3,
                    pageSize: 25,
                },
            });
            callback({
                success: true,
                data: {
                    items: [{ id: 'audio:voice.wav', type: 'audio', filename: 'voice.wav' }],
                    total: 51,
                    diskUsage: { total: 100, used: 20, available: 80 },
                },
            });
        }),
    };
    const store = createStore();

    await store.dispatch(fetchFiles({
        socket,
        filters: { showAudio: true },
        sortBy: 'modified',
        sortOrder: 'desc',
        page: 3,
        pageSize: 25,
    })).unwrap();

    expect(store.getState().filebrowser.files).toEqual([
        { id: 'audio:voice.wav', type: 'audio', filename: 'voice.wav' },
    ]);
    expect(store.getState().filebrowser.total).toBe(51);
});

test('only removes acknowledged library deletions', async () => {
    const socket = {
        connected: true,
        emit: vi.fn((event, payload, callback) => {
            expect(event).toBe('api.call');
            expect(payload).toEqual({
                cmd: 'filebrowser.delete',
                data: { ids: ['audio:voice.wav'] },
            });
            callback({ success: false, error: 'Item not found' });
        }),
    };
    const store = createStore();

    await expect(store.dispatch(deleteLibraryItems({ socket, ids: ['audio:voice.wav'] })).unwrap())
        .rejects.toThrow('Item not found');
    expect(store.getState().filebrowser.files).toEqual([]);
});

test('changing responsive page size restarts paging', () => {
    const store = createStore();
    store.dispatch({ type: 'filebrowser/setPage', payload: 4 });
    store.dispatch(setPageSize(25));

    expect(store.getState().filebrowser.pageSize).toBe(25);
    expect(store.getState().filebrowser.page).toBe(1);
});

test('purge preview aggregates are stored per cutoff', async () => {
    const preview = {
        cutoff: '2026-01-01T00:00:00.000Z',
        items: 7,
        files: 40,
        bytes: 1234,
        skippedInProgress: 2,
        categories: { observation: { items: 1, files: 30, bytes: 900 } },
        oldest: '2024-01-01T00:00:00+00:00',
        newest: '2026-06-01T00:00:00+00:00',
    };
    const socket = {
        connected: true,
        emit: vi.fn((event, payload, callback) => {
            expect(payload.cmd).toBe('filebrowser.purge-preview');
            callback({ success: true, data: preview });
        }),
    };
    const store = createStore();

    await store.dispatch(fetchPurgePreview({ socket, cutoff: preview.cutoff })).unwrap();

    expect(store.getState().filebrowser.purge.preview).toEqual(preview);
    expect(store.getState().filebrowser.purge.previewLoading).toBe(false);
});

test('purge lifecycle mirrors socket progress', async () => {
    const socket = {
        connected: true,
        emit: vi.fn((event, payload, callback) => {
            expect(payload.cmd).toBe('filebrowser.purge');
            callback({ success: true, data: { started: true } });
        }),
    };
    const store = createStore();

    await store.dispatch(startLibraryPurge({ socket, cutoff: '2026-01-01T00:00:00.000Z' })).unwrap();
    expect(store.getState().filebrowser.purge.running).toBe(true);

    store.dispatch(purgeProgressUpdated({ done: 3, total: 10, deleted: 3, files: 9, bytesFreed: 300, current: 'bundle' }));
    expect(store.getState().filebrowser.purge.progress.done).toBe(3);

    store.dispatch(purgeFinished({ phase: 'done', success: true, deleted: 10, files: 200, bytesFreed: 4000, failed: [] }));
    expect(store.getState().filebrowser.purge.running).toBe(false);
    expect(store.getState().filebrowser.purge.done.success).toBe(true);
    expect(store.getState().filebrowser.purge.progress).toBeNull();

    store.dispatch(clearPurgeState());
    expect(store.getState().filebrowser.purge.done).toBeNull();
});

test('a start acknowledgement cannot resurrect an already completed purge', () => {
    const store = createStore();
    const done = { phase: 'done', success: true, deleted: 0, files: 0, bytesFreed: 0, failed: [] };

    store.dispatch(startLibraryPurge.pending('request-1', {}));
    store.dispatch(purgeFinished(done));
    store.dispatch(startLibraryPurge.fulfilled({ started: true }, 'request-1', {}));

    expect(store.getState().filebrowser.purge.running).toBe(false);
    expect(store.getState().filebrowser.purge.done).toEqual(done);
});

test('a completed purge invalidates preview replies from before deletion', () => {
    const store = createStore();
    const cutoff = '2026-01-01T00:00:00.000Z';

    store.dispatch(fetchPurgePreview.pending('old-preview', { cutoff }));
    store.dispatch(purgeFinished({ success: true, deleted: 1, files: 1, bytesFreed: 100, failed: [] }));
    store.dispatch(fetchPurgePreview.fulfilled({ cutoff, items: 1 }, 'old-preview', { cutoff }));
    expect(store.getState().filebrowser.purge.preview).toBeNull();

    store.dispatch(fetchPurgePreview.pending('new-preview', { cutoff }));
    store.dispatch(fetchPurgePreview.fulfilled({ cutoff, items: 0 }, 'new-preview', { cutoff }));
    expect(store.getState().filebrowser.purge.preview.items).toBe(0);
});

test('reopening the dialog preserves purge progress', () => {
    const store = createStore();
    store.dispatch(purgeProgressUpdated({ done: 1, total: 2, current: 'capture' }));
    store.dispatch(clearPurgeState());

    expect(store.getState().filebrowser.purge.running).toBe(true);
    expect(store.getState().filebrowser.purge.progress.current).toBe('capture');
});
