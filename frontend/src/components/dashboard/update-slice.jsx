import { createSlice, createAsyncThunk } from '@reduxjs/toolkit';

export const fetchUpdateCheck = createAsyncThunk(
    'updateCheck/fetchUpdateCheck',
    async ({ socket, force = false }, { rejectWithValue }) => {
        try {
            if (!socket?.connected) {
                throw new Error('Socket is disconnected.');
            }
            const reply = await socket.timeout(15000).emitWithAck('api.call', {
                cmd: 'system.update_check',
                data: { refresh: force },
            });
            if (!reply?.success || !reply.data) {
                throw new Error(reply?.error || 'Update check failed');
            }
            return reply.data;
        } catch (error) {
            return rejectWithValue(error instanceof Error ? error.message : 'Update check failed');
        }
    }
);

const updateSlice = createSlice({
    name: 'updateCheck',
    initialState: {
        data: {
            currentVersion: null,
            latestVersion: null,
            latestTag: null,
            latestUrl: null,
            publishedAt: null,
            checkedAt: null,
            isUpdateAvailable: false,
        },
        loading: false,
        error: null,
        lastChecked: null,
        lastAttempted: null,
    },
    reducers: {},
    extraReducers: (builder) => {
        builder
            .addCase(fetchUpdateCheck.pending, (state) => {
                state.loading = true;
                state.error = null;
                state.lastAttempted = Date.now();
            })
            .addCase(fetchUpdateCheck.fulfilled, (state, action) => {
                state.loading = false;
                state.data = action.payload || state.data;
                state.lastChecked = Date.now();
            })
            .addCase(fetchUpdateCheck.rejected, (state, action) => {
                state.loading = false;
                state.error = action.payload || action.error?.message || 'Update check failed';
            });
    },
});

export default updateSlice.reducer;
