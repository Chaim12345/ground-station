import { createSlice, createAsyncThunk } from '@reduxjs/toolkit';

/**
 * Fetch the live radar view (stations above-horizon satellite positions)
 * from the backend. Shape: { generated_at, compute_ms, stations, targets, positions }
 */
export const fetchRadarView = createAsyncThunk(
    'radar/fetchRadarView',
    async ({ socket }, { rejectWithValue }) => {
        try {
            return await new Promise((resolve, reject) => {
                socket.emit("api.call", {
                    cmd: 'get-radar-view',
                    data: null
                }, res => {
                    if (res && res.success) {
                        resolve(res.data);
                    } else {
                        reject(new Error('Failed to fetch radar view'));
                    }
                });
            });
        } catch (error) {
            return rejectWithValue(error.message);
        }
    }
);

// Live-now panel: running + upcoming observations (same emit as the scheduler)
export const fetchDashboardObservations = createAsyncThunk(
    'radar/fetchDashboardObservations',
    async ({ socket }, { rejectWithValue }) => {
        try {
            return await new Promise((resolve, reject) => {
                socket.emit("api.call", {
                    cmd: 'get-scheduled-observations',
                    data: null
                }, res => {
                    if (res && res.success) {
                        resolve(res.data);
                    } else {
                        reject(new Error('Failed to fetch scheduled observations'));
                    }
                });
            });
        } catch (error) {
            return rejectWithValue(error.message);
        }
    }
);

// Tracked targets for the stations & tracking panel
export const fetchDashboardTrackers = createAsyncThunk(
    'radar/fetchDashboardTrackers',
    async ({ socket }, { rejectWithValue }) => {
        try {
            return await new Promise((resolve, reject) => {
                socket.emit("api.call", {
                    cmd: 'get-tracker-instances',
                    data: null
                }, res => {
                    if (res && res.success) {
                        resolve(res.data || {});
                    } else {
                        reject(new Error('Failed to fetch tracker instances'));
                    }
                });
            });
        } catch (error) {
            return rejectWithValue(error.message);
        }
    }
);

// Decoded artifacts come from the shared library query with a decoded-only
// filter (the backend owns filtering/sorting/pagination, see filebrowser-slice).
export const fetchDashboardDecoded = createAsyncThunk(
    'radar/fetchDashboardDecoded',
    async ({ socket }, { rejectWithValue }) => {
        try {
            return await new Promise((resolve, reject) => {
                socket.emit("api.call", {
                    cmd: 'filebrowser.query',
                    data: {
                        filters: {
                            showRecordings: false,
                            showSnapshots: false,
                            showDecoded: true,
                            showObservations: false,
                            showAudio: false,
                            showTranscriptions: false,
                        },
                        sortBy: 'created',
                        sortOrder: 'desc',
                        page: 1,
                        pageSize: 8,
                    }
                }, res => {
                    if (res && res.success) {
                        resolve(res.data);
                    } else {
                        reject(new Error('Failed to fetch decoded output'));
                    }
                });
            });
        } catch (error) {
            return rejectWithValue(error.message);
        }
    }
);

// Recordings use the legacy pub/sub listing: emit without a callback, the
// response arrives as a file_browser_state event and the global handler in
// useSocketEventHandlers stores it in filebrowser.playbackRecordings.
export const requestDashboardRecordings = createAsyncThunk(
    'radar/requestDashboardRecordings',
    async ({ socket }, { rejectWithValue }) => {
        try {
            socket.emit("api.call", {
                cmd: "filebrowser.list-recordings",
                data: {}
            });
            return { pending: true };
        } catch (error) {
            return rejectWithValue(error.message || 'Failed to request recordings');
        }
    }
);

const dashboardInitial = {
    observations: null,
    trackers: null,
    decoded: null,
    observationsLoaded: false,
    trackersLoaded: false,
    decodedLoaded: false,
    recordingsRequested: false,
    observationsAt: null,
    trackersAt: null,
    decodedAt: null,
};

const initialState = {
    data: null,
    loading: false,
    error: null,
    lastFetched: null,
    dashboard: dashboardInitial,
};

const radarSlice = createSlice({
    name: 'radar',
    initialState,
    reducers: {},
    extraReducers: (builder) => {
        builder
            .addCase(fetchRadarView.pending, (state) => {
                state.loading = true;
                state.error = null;
            })
            .addCase(fetchRadarView.fulfilled, (state, action) => {
                state.loading = false;
                state.data = action.payload;
                state.lastFetched = Date.now();
            })
            .addCase(fetchRadarView.rejected, (state, action) => {
                state.loading = false;
                state.error = action.payload;
            })
            .addCase(fetchDashboardObservations.fulfilled, (state, action) => {
                state.dashboard.observations = action.payload;
                state.dashboard.observationsLoaded = true;
                state.dashboard.observationsAt = Date.now();
            })
            .addCase(fetchDashboardTrackers.fulfilled, (state, action) => {
                state.dashboard.trackers = action.payload;
                state.dashboard.trackersLoaded = true;
                state.dashboard.trackersAt = Date.now();
            })
            .addCase(fetchDashboardDecoded.fulfilled, (state, action) => {
                state.dashboard.decoded = action.payload;
                state.dashboard.decodedLoaded = true;
                state.dashboard.decodedAt = Date.now();
            })
            .addCase(requestDashboardRecordings.pending, (state) => {
                state.dashboard.recordingsRequested = true;
            });
    },
});

export default radarSlice.reducer;
