import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { useDispatch, useSelector } from 'react-redux';
import { useTranslation } from 'react-i18next';
import { alpha } from '@mui/material/styles';
import {
    Alert,
    Box,
    Card,
    Chip,
    CircularProgress,
    Grid,
    IconButton,
    ListItemButton,
    ListItemIcon,
    ListItemText,
    Paper,
    Select,
    MenuItem,
    Skeleton,
    Tooltip,
    Typography,
} from '@mui/material';
import RefreshIcon from '@mui/icons-material/Refresh';
import { useSocket } from '../common/socket.jsx';
import {
    fetchRadarView,
    fetchDashboardObservations,
    fetchDashboardTrackers,
    fetchDashboardDecoded,
    requestDashboardRecordings,
} from './radar-slice.jsx';

const REFRESH_INTERVAL_MS = 15000;
const OBSERVATIONS_INTERVAL_MS = 15000;
const TRACKERS_INTERVAL_MS = 15000;
const LIBRARY_INTERVAL_MS = 30000;

// Max rows shown per list panel before the "+N more" note
const MAX_PANEL_ITEMS = 8;

// Blip colors (fixed so they stay readable on both light and dark plots)
const COLOR_MONITORED = '#f44336';
const COLOR_TRANSMITTER = '#4caf50';
const COLOR_OTHER = '#90a4ae';

// Small deterministic palette used only for the station list dots
const STATION_COLORS = ['#7e57c2', '#26a69a', '#ef6c00', '#5c6bc0', '#d81b60', '#00897b', '#8d6e63', '#5d4037'];

const blipColor = (blip) => {
    if (blip.monitored) return COLOR_MONITORED;
    if ((blip.transmitter_count ?? 0) > 0) return COLOR_TRANSMITTER;
    return COLOR_OTHER;
};

const formatBytes = (bytes) => {
    const value = Number(bytes);
    if (!Number.isFinite(value) || value <= 0) return null;
    const units = ['B', 'KB', 'MB', 'GB', 'TB'];
    let size = value;
    let unit = 0;
    while (size >= 1024 && unit < units.length - 1) {
        size /= 1024;
        unit += 1;
    }
    return `${size >= 10 || unit === 0 ? Math.round(size) : size.toFixed(1)} ${units[unit]}`;
};

// Compact relative age ("2m ago") for panel rows and the header indicator
const formatAge = (isoOrMs, nowMs) => {
    const ms = typeof isoOrMs === 'number' ? isoOrMs : new Date(isoOrMs ?? 0).getTime();
    if (!Number.isFinite(ms) || ms <= 0) return null;
    const seconds = Math.max(0, Math.round((nowMs - ms) / 1000));
    if (seconds < 60) return `${seconds}s`;
    const minutes = Math.floor(seconds / 60);
    if (minutes < 60) return `${minutes}m`;
    const hours = Math.floor(minutes / 60);
    if (hours < 24) return `${hours}h`;
    return `${Math.floor(hours / 24)}d`;
};

// Local clock time "12:14" for observation windows
const formatClock = (iso) => {
    const date = iso ? new Date(iso) : null;
    if (!date || Number.isNaN(date.getTime())) return '—';
    return date.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
};

// Recording names look like `<Satellite>_<timestamp>[_<recorder>]`
const satelliteFromName = (name) => {
    const text = String(name ?? '');
    if (!text.includes('_')) return null;
    return text.split('_')[0] || null;
};

export default function RadarPage() {
    const { t } = useTranslation('radar');
    const { socket } = useSocket();
    const dispatch = useDispatch();
    const { data, loading, error, lastFetched, dashboard } = useSelector((state) => state.radar);
    const playbackRecordings = useSelector((state) => state.filebrowser?.playbackRecordings ?? null);

    const [selectedStation, setSelectedStation] = useState('all');
    const [now, setNow] = useState(() => Date.now());
    // Recordings arrive via the global pub/sub event; this timestamp marks the
    // panel loaded and feeds its "updated" label.
    const [recordingsAt, setRecordingsAt] = useState(null);

    const inFlightRef = useRef(false);
    const observationsInFlight = useRef(false);
    const trackersInFlight = useRef(false);
    const decodedInFlight = useRef(false);

    const refresh = useCallback(() => {
        if (!socket || inFlightRef.current) return;
        inFlightRef.current = true;
        dispatch(fetchRadarView({ socket }))
            .unwrap()
            .catch(() => {})
            .finally(() => {
                inFlightRef.current = false;
            });
    }, [dispatch, socket]);

    const refreshObservations = useCallback(() => {
        if (!socket || observationsInFlight.current) return;
        observationsInFlight.current = true;
        dispatch(fetchDashboardObservations({ socket }))
            .unwrap()
            .catch(() => {})
            .finally(() => {
                observationsInFlight.current = false;
            });
    }, [dispatch, socket]);

    const refreshTrackers = useCallback(() => {
        if (!socket || trackersInFlight.current) return;
        trackersInFlight.current = true;
        dispatch(fetchDashboardTrackers({ socket }))
            .unwrap()
            .catch(() => {})
            .finally(() => {
                trackersInFlight.current = false;
            });
    }, [dispatch, socket]);

    const refreshDecoded = useCallback(() => {
        if (!socket || decodedInFlight.current) return;
        decodedInFlight.current = true;
        dispatch(fetchDashboardDecoded({ socket }))
            .unwrap()
            .catch(() => {})
            .finally(() => {
                decodedInFlight.current = false;
            });
    }, [dispatch, socket]);

    // Recordings listing is pub/sub: emit the request, the response lands in
    // filebrowser.playbackRecordings via useSocketEventHandlers.
    const refreshRecordings = useCallback(() => {
        if (!socket) return;
        dispatch(requestDashboardRecordings({ socket }))
            .unwrap()
            .catch(() => {});
    }, [dispatch, socket]);

    // One polling effect per data source; all intervals cleared on unmount
    useEffect(() => {
        refresh();
        const interval = setInterval(refresh, REFRESH_INTERVAL_MS);
        return () => clearInterval(interval);
    }, [refresh]);

    useEffect(() => {
        refreshObservations();
        const interval = setInterval(refreshObservations, OBSERVATIONS_INTERVAL_MS);
        return () => clearInterval(interval);
    }, [refreshObservations]);

    useEffect(() => {
        refreshTrackers();
        const interval = setInterval(refreshTrackers, TRACKERS_INTERVAL_MS);
        return () => clearInterval(interval);
    }, [refreshTrackers]);

    useEffect(() => {
        refreshDecoded();
        const interval = setInterval(refreshDecoded, LIBRARY_INTERVAL_MS);
        return () => clearInterval(interval);
    }, [refreshDecoded]);

    useEffect(() => {
        refreshRecordings();
        const interval = setInterval(refreshRecordings, LIBRARY_INTERVAL_MS);
        return () => clearInterval(interval);
    }, [refreshRecordings]);

    // Local listener only marks the recordings panel loaded; the store update
    // itself is done by the global handler.
    useEffect(() => {
        if (!socket) return undefined;
        const onFileBrowserState = (state) => {
            if (state?.action === 'list-recordings') setRecordingsAt(Date.now());
        };
        socket.on('file_browser_state', onFileBrowserState);
        return () => socket.off('file_browser_state', onFileBrowserState);
    }, [socket]);

    // 1s heartbeat so the "Xs ago" labels stay live
    useEffect(() => {
        const interval = setInterval(() => setNow(Date.now()), 1000);
        return () => clearInterval(interval);
    }, []);

    const refreshAll = useCallback(() => {
        refresh();
        refreshObservations();
        refreshTrackers();
        refreshDecoded();
        refreshRecordings();
    }, [refresh, refreshObservations, refreshTrackers, refreshDecoded, refreshRecordings]);

    const stations = useMemo(
        () => [...(data?.stations ?? [])].sort((a, b) => (b.target_count ?? 0) - (a.target_count ?? 0)),
        [data]
    );

    const stationNameById = useMemo(
        () => Object.fromEntries(stations.map((s) => [s.id, s.name ?? s.id])),
        [stations]
    );

    // Blips for the plot: single station view, or merged union across all
    // stations (dedup by norad_id keeping the highest elevation).
    const blips = useMemo(() => {
        const positions = data?.positions ?? {};
        const targets = data?.targets ?? {};
        const decorate = (p, seenBy) => ({
            ...p,
            ...(targets[p.norad_id] ?? {}),
            seenBy,
        });

        if (selectedStation !== 'all') {
            return (positions[selectedStation] ?? []).map((p) => decorate(p, [selectedStation]));
        }

        const byNorad = new Map();
        for (const [stationId, list] of Object.entries(positions)) {
            for (const p of list ?? []) {
                if (p?.norad_id == null) continue;
                const existing = byNorad.get(p.norad_id);
                if (!existing) {
                    byNorad.set(p.norad_id, decorate(p, [stationId]));
                } else {
                    // Keep the geometry of the highest-elevation sighting
                    if ((p.el ?? 0) > (existing.el ?? 0)) {
                        existing.az = p.az;
                        existing.el = p.el;
                        existing.range_km = p.range_km;
                    }
                    if (!existing.seenBy.includes(stationId)) existing.seenBy.push(stationId);
                }
            }
        }
        return [...byNorad.values()];
    }, [data, selectedStation]);

    const monitoredCount = useMemo(
        () => Object.values(data?.targets ?? {}).filter((tgt) => tgt?.monitored).length,
        [data]
    );

    // Live now: running first, then by start time; terminal statuses dropped
    const observations = useMemo(() => {
        const raw = Array.isArray(dashboard?.observations) ? dashboard.observations : [];
        const terminal = ['completed', 'failed', 'cancelled'];
        const active = raw.filter((obs) => !terminal.includes(obs?.status ?? 'scheduled'));
        const startMs = (obs) => new Date(obs?.task_start ?? obs?.pass?.event_start ?? 0).getTime() || 0;
        return [...active].sort((a, b) => {
            const aRunning = a?.status === 'running' ? 0 : 1;
            const bRunning = b?.status === 'running' ? 0 : 1;
            if (aRunning !== bRunning) return aRunning - bRunning;
            return startMs(a) - startMs(b);
        });
    }, [dashboard?.observations]);

    const recordings = useMemo(() => {
        const raw = Array.isArray(playbackRecordings) ? playbackRecordings : [];
        return [...raw]
            .sort((a, b) => (new Date(b?.created ?? 0).getTime() || 0) - (new Date(a?.created ?? 0).getTime() || 0))
            .slice(0, MAX_PANEL_ITEMS);
    }, [playbackRecordings]);

    const decoded = useMemo(
        () => (Array.isArray(dashboard?.decoded?.items) ? dashboard.decoded.items.slice(0, MAX_PANEL_ITEMS) : []),
        [dashboard?.decoded]
    );

    const trackers = useMemo(
        () => (Array.isArray(dashboard?.trackers?.instances) ? dashboard.trackers.instances : []),
        [dashboard?.trackers]
    );

    // Global "updated" indicator: the freshest of all panel timestamps
    const lastUpdateMs = useMemo(() => {
        const stamps = [lastFetched, dashboard?.observationsAt, dashboard?.trackersAt, dashboard?.decodedAt, recordingsAt]
            .filter((value) => typeof value === 'number' && value > 0);
        return stamps.length ? Math.max(...stamps) : null;
    }, [lastFetched, dashboard?.observationsAt, dashboard?.trackersAt, dashboard?.decodedAt, recordingsAt]);

    const lastUpdateLabel = lastFetched ? new Date(lastFetched).toLocaleTimeString() : '—';
    const globalAge = lastUpdateMs ? formatAge(lastUpdateMs, now) : null;
    const anyLoading = loading || !data;

    return (
        <Box sx={{ display: 'flex', flexDirection: 'column', gap: 2, height: '100%', minWidth: 0 }}>
            {/* Header row */}
            <Box sx={{ display: 'flex', alignItems: 'center', gap: 1.5, flexWrap: 'wrap' }}>
                <Typography variant="h5">{t('live_dashboard', 'Live Dashboard')}</Typography>
                <Typography variant="body2" color="text.secondary">
                    {globalAge
                        ? t('updated_ago', { age: globalAge, defaultValue: `Updated ${globalAge} ago` })
                        : t('last_update', { time: lastUpdateLabel })}
                </Typography>
                {data?.compute_ms != null && (
                    <Chip size="small" label={t('compute_ms', { ms: data.compute_ms })} />
                )}
                <IconButton onClick={refreshAll} size="small" disabled={anyLoading} aria-label={t('refresh')}>
                    <RefreshIcon />
                </IconButton>
            </Box>

            <Grid container spacing={2} alignItems="stretch">
                {/* Hero: radar plot + station selector */}
                <Grid size={{ xs: 12, lg: 8 }}>
                    <Box sx={{ display: 'flex', flexDirection: 'column', gap: 1.5, minWidth: 0 }}>
                        {error && <Alert severity="error">{t('load_error')}</Alert>}

                        {!data && loading && (
                            <Box sx={{ display: 'flex', justifyContent: 'center', py: 6 }}>
                                <CircularProgress />
                            </Box>
                        )}

                        {data && stations.length === 0 && (
                            <Alert severity="info">{t('empty')}</Alert>
                        )}

                        {data && stations.length > 0 && (
                            <>
                                <Select
                                    size="small"
                                    value={selectedStation}
                                    onChange={(e) => setSelectedStation(e.target.value)}
                                    sx={{ minWidth: 260, maxWidth: '100%', bgcolor: 'background.paper' }}
                                >
                                    <MenuItem value="all">{t('all_stations')}</MenuItem>
                                    {stations.map((s) => (
                                        <MenuItem key={s.id} value={s.id}>
                                            {`${s.name ?? s.id} (${s.target_count ?? 0})`}
                                        </MenuItem>
                                    ))}
                                </Select>
                                <RadarPlot blips={blips} stationNameById={stationNameById} />
                                <Box sx={{ display: 'flex', alignItems: 'center', gap: 3, flexWrap: 'wrap' }}>
                                    <Typography variant="body2">
                                        {t('monitored_count', { count: monitoredCount })}
                                    </Typography>
                                    <LegendItem color={COLOR_MONITORED} label={t('monitored')} />
                                    <LegendItem color={COLOR_TRANSMITTER} label={t('has_transmitter')} />
                                    <LegendItem color={COLOR_OTHER} label={t('other')} />
                                    <Typography variant="caption" color="text.secondary">
                                        {t('note')}
                                    </Typography>
                                </Box>
                            </>
                        )}
                    </Box>
                </Grid>

                {/* Live now: running + upcoming observations */}
                <Grid size={{ xs: 12, lg: 4 }}>
                    <DashboardPanel title={t('panel_live_now', 'Live now')} updatedAt={ageLabel(dashboard?.observationsAt, now, t)}>
                        {!dashboard?.observationsLoaded ? (
                            <PanelSkeleton />
                        ) : observations.length === 0 ? (
                            <EmptyText text={t('no_observations', 'No running or upcoming observations')} />
                        ) : (
                            <>
                                {observations.slice(0, MAX_PANEL_ITEMS).map((obs) => (
                                    <ObservationRow key={obs?.id ?? `${obs?.satellite?.norad_id}-${obs?.pass?.event_start}`} obs={obs} t={t} />
                                ))}
                                {observations.length > MAX_PANEL_ITEMS && (
                                    <Typography variant="caption" color="text.secondary" sx={{ display: 'block', pt: 1 }}>
                                        {t('more_items', { count: observations.length - MAX_PANEL_ITEMS, defaultValue: `+${observations.length - MAX_PANEL_ITEMS} more` })}
                                    </Typography>
                                )}
                            </>
                        )}
                    </DashboardPanel>
                </Grid>

                {/* Recordings: recent captures */}
                <Grid size={{ xs: 12, md: 6, lg: 4 }}>
                    <DashboardPanel title={t('panel_recordings', 'Recordings')} updatedAt={ageLabel(recordingsAt, now, t)}>
                        {!recordingsAt ? (
                            <PanelSkeleton />
                        ) : recordings.length === 0 ? (
                            <EmptyText text={t('no_recordings', 'No recordings yet')} />
                        ) : (
                            recordings.map((rec) => {
                                const satellite = rec?.satellite_name ?? satelliteFromName(rec?.name);
                                const size = formatBytes(rec?.size ?? rec?.data_size);
                                const age = formatAge(rec?.created, now);
                                return (
                                    <Box key={rec?.name ?? rec?.created} sx={{ py: 0.5, borderBottom: 1, borderColor: 'divider', '&:last-child': { borderBottom: 0 } }}>
                                        <Typography variant="body2" noWrap title={rec?.name ?? ''}>
                                            {rec?.name ?? '—'}
                                        </Typography>
                                        <Box sx={{ display: 'flex', alignItems: 'center', gap: 1, flexWrap: 'wrap' }}>
                                            {satellite && (
                                                <Chip size="small" label={satellite} sx={{ height: 20, fontSize: '0.7rem' }} />
                                            )}
                                            {size && (
                                                <Typography variant="caption" color="text.secondary">{size}</Typography>
                                            )}
                                            {age && (
                                                <Typography variant="caption" color="text.secondary">
                                                    {t('ago_suffix', { age, defaultValue: `${age} ago` })}
                                                </Typography>
                                            )}
                                        </Box>
                                    </Box>
                                );
                            })
                        )}
                    </DashboardPanel>
                </Grid>

                {/* Decoded output: recent decoded artifacts */}
                <Grid size={{ xs: 12, md: 6, lg: 4 }}>
                    <DashboardPanel title={t('panel_decoded', 'Decoded output')} updatedAt={ageLabel(dashboard?.decodedAt, now, t)}>
                        {!dashboard?.decodedLoaded ? (
                            <PanelSkeleton />
                        ) : decoded.length === 0 ? (
                            <EmptyText text={t('no_decoded', 'No decoded output yet')} />
                        ) : (
                            decoded.map((item) => {
                                const name = item?.name ?? item?.filename ?? item?.foldername ?? '—';
                                const satellite = item?.satellite_name ?? satelliteFromName(name);
                                const age = formatAge(item?.created, now);
                                return (
                                    <Box key={item?.id ?? name} sx={{ py: 0.5, borderBottom: 1, borderColor: 'divider', '&:last-child': { borderBottom: 0 } }}>
                                        <Typography variant="body2" noWrap title={name}>
                                            {name}
                                        </Typography>
                                        <Box sx={{ display: 'flex', alignItems: 'center', gap: 1, flexWrap: 'wrap' }}>
                                            {satellite && (
                                                <Chip size="small" label={satellite} sx={{ height: 20, fontSize: '0.7rem' }} />
                                            )}
                                            {item?.decoder_type && (
                                                <Typography variant="caption" color="text.secondary">{item.decoder_type}</Typography>
                                            )}
                                            {age && (
                                                <Typography variant="caption" color="text.secondary">
                                                    {t('ago_suffix', { age, defaultValue: `${age} ago` })}
                                                </Typography>
                                            )}
                                        </Box>
                                    </Box>
                                );
                            })
                        )}
                    </DashboardPanel>
                </Grid>

                {/* Stations & tracking */}
                <Grid size={{ xs: 12, lg: 4 }}>
                    <DashboardPanel title={t('panel_stations', 'Stations & tracking')} updatedAt={ageLabel(lastFetched, now, t)}>
                        {stations.length === 0 ? (
                            <EmptyText text={t('empty')} />
                        ) : (
                            <Box sx={{ display: 'flex', flexDirection: 'column' }}>
                                {stations.map((s, i) => (
                                    <ListItemButton
                                        key={s.id}
                                        selected={selectedStation === s.id}
                                        onClick={() => setSelectedStation(s.id)}
                                        dense
                                    >
                                        <ListItemIcon sx={{ minWidth: 32 }}>
                                            <Box
                                                sx={{
                                                    width: 10,
                                                    height: 10,
                                                    borderRadius: '50%',
                                                    bgcolor: STATION_COLORS[i % STATION_COLORS.length],
                                                }}
                                            />
                                        </ListItemIcon>
                                        <ListItemText
                                            primary={s.name ?? s.id}
                                            secondary={`${s.target_count ?? 0} ${t('targets')}`}
                                        />
                                        {(s.current_clients ?? 0) > 0 && (
                                            <Chip size="small" color="warning" label={t('busy', 'busy')} sx={{ height: 20, fontSize: '0.7rem' }} />
                                        )}
                                    </ListItemButton>
                                ))}
                            </Box>
                        )}

                        <Typography variant="subtitle2" sx={{ pt: 1.5, pb: 0.5 }}>
                            {t('tracked', 'Tracked targets')}
                        </Typography>
                        {!dashboard?.trackersLoaded ? (
                            <PanelSkeleton rows={2} />
                        ) : trackers.length === 0 ? (
                            <EmptyText text={t('no_trackers', 'No tracked targets')} />
                        ) : (
                            trackers.slice(0, MAX_PANEL_ITEMS).map((inst, i) => {
                                const name = inst?.target_name ?? inst?.name ?? inst?.norad_id ?? inst?.norad ?? '—';
                                const az = inst?.az != null ? `${Number(inst.az).toFixed(1)}°` : null;
                                const el = inst?.el != null ? `${Number(inst.el).toFixed(1)}°` : null;
                                return (
                                    <Box
                                        key={inst?.tracker_id ?? inst?.target_name ?? i}
                                        sx={{ display: 'flex', alignItems: 'center', gap: 1, py: 0.25 }}
                                    >
                                        <Box sx={{ width: 8, height: 8, borderRadius: '50%', bgcolor: COLOR_TRANSMITTER }} />
                                        <Typography variant="body2" noWrap sx={{ flex: 1, minWidth: 0 }}>
                                            {name}
                                            {inst?.norad_id != null && (
                                                <Typography component="span" variant="caption" color="text.secondary" sx={{ ml: 1 }}>
                                                    {inst.norad_id}
                                                </Typography>
                                            )}
                                        </Typography>
                                        {(az || el) && (
                                            <Typography variant="caption" color="text.secondary">
                                                {[az != null && `Az ${az}`, el != null && `El ${el}`].filter(Boolean).join(' · ')}
                                            </Typography>
                                        )}
                                    </Box>
                                );
                            })
                        )}
                    </DashboardPanel>
                </Grid>
            </Grid>
        </Box>
    );
}

function ageLabel(at, now, t) {
    const age = at ? formatAge(at, now) : null;
    return age ? t('ago_suffix', { age, defaultValue: `${age} ago` }) : '—';
}

// Shared panel chrome: header with title + last-update, scrollable body
function DashboardPanel({ title, updatedAt, children }) {
    return (
        <Card sx={{ display: 'flex', flexDirection: 'column', height: '100%', minWidth: 0 }}>
            <Box sx={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 1, px: 2, pt: 1.5, pb: 1 }}>
                <Typography variant="subtitle1">{title}</Typography>
                <Typography variant="caption" color="text.secondary" sx={{ whiteSpace: 'nowrap' }}>
                    {updatedAt}
                </Typography>
            </Box>
            <Box sx={{ px: 2, pb: 1.5, overflow: 'auto', flex: 1, minHeight: 0, maxHeight: 560 }}>
                {children}
            </Box>
        </Card>
    );
}

function PanelSkeleton({ rows = 4 }) {
    return (
        <Box sx={{ display: 'flex', flexDirection: 'column', gap: 1, py: 0.5 }}>
            {Array.from({ length: rows }).map((_, i) => (
                <Skeleton key={i} variant="text" height={32} />
            ))}
        </Box>
    );
}

function EmptyText({ text }) {
    return (
        <Typography variant="body2" color="text.secondary" sx={{ py: 2 }}>
            {text}
        </Typography>
    );
}

function ObservationRow({ obs, t }) {
    const running = obs?.status === 'running';
    const statusKey = String(obs?.status ?? 'scheduled').toUpperCase();
    const satellite = obs?.satellite?.name ?? obs?.name ?? obs?.satellite?.norad_id ?? '—';
    const norad = obs?.satellite?.norad_id;
    const start = formatClock(obs?.task_start ?? obs?.pass?.event_start);
    const end = formatClock(obs?.pass?.event_end);
    const session = obs?.sessions?.[0];
    const sdr = session?.sdr?.name ?? session?.sdr_id ?? null;
    const sessionCount = Array.isArray(obs?.sessions) ? obs.sessions.length : 0;

    return (
        <Box
            sx={{
                px: 1,
                py: 0.75,
                my: 0.5,
                borderRadius: 1,
                bgcolor: running ? (theme) => alpha(theme.palette.success.main, 0.12) : 'transparent',
                border: 1,
                borderColor: running ? 'success.main' : 'divider',
            }}
        >
            <Box sx={{ display: 'flex', alignItems: 'center', gap: 1, justifyContent: 'space-between' }}>
                <Typography variant="body2" noWrap sx={{ flex: 1, minWidth: 0, fontWeight: running ? 600 : 400 }}>
                    {satellite}
                    {norad != null && (
                        <Typography component="span" variant="caption" color="text.secondary" sx={{ ml: 1 }}>
                            {norad}
                        </Typography>
                    )}
                </Typography>
                <StatusChip running={running} label={statusKey} t={t} />
            </Box>
            <Box sx={{ display: 'flex', alignItems: 'center', gap: 1, flexWrap: 'wrap' }}>
                <Typography variant="caption" color="text.secondary">
                    {`${start} → ${end}`}
                </Typography>
                {sdr && (
                    <Typography variant="caption" color="text.secondary">
                        {sdr}
                        {sessionCount > 1 && ` +${sessionCount - 1}`}
                    </Typography>
                )}
            </Box>
        </Box>
    );
}

function StatusChip({ running, label, t }) {
    if (running) {
        return (
            <Chip
                size="small"
                color="success"
                label={t('status_running', 'RUNNING')}
                sx={{
                    height: 20,
                    fontSize: '0.7rem',
                    '@keyframes running-pulse': {
                        '0%': { opacity: 1 },
                        '50%': { opacity: 0.45 },
                        '100%': { opacity: 1 },
                    },
                    animation: 'running-pulse 1.6s ease-in-out infinite',
                }}
            />
        );
    }
    const scheduled = label === 'SCHEDULED';
    return (
        <Chip
            size="small"
            color={scheduled ? 'primary' : 'default'}
            label={scheduled ? t('status_scheduled', 'SCHEDULED') : label}
            sx={{ height: 20, fontSize: '0.7rem' }}
        />
    );
}

function LegendItem({ color, label }) {
    return (
        <Box sx={{ display: 'flex', alignItems: 'center', gap: 1, py: 0.25 }}>
            <Box sx={{ width: 10, height: 10, borderRadius: '50%', bgcolor: color }} />
            <Typography variant="body2">{label}</Typography>
        </Box>
    );
}

const PLOT_SIZE = 600;
const CENTER = PLOT_SIZE / 2;
const RADIUS = 260;

// Elevation (0..90) -> plot radius; el=90 at center, el=0 at the outer edge
const elToRadius = (el) => ((90 - Math.min(Math.max(el ?? 0, 0), 90)) / 90) * RADIUS;

// Azimuth (0=N up, clockwise east) -> SVG x/y around the center
const azToXY = (az, r) => {
    const rad = ((az ?? 0) * Math.PI) / 180;
    return [CENTER + r * Math.sin(rad), CENTER - r * Math.cos(rad)];
};

const CARDINALS = [['N', 0], ['E', 90], ['S', 180], ['W', 270]];

function RadarPlot({ blips, stationNameById }) {

    return (
        <Paper sx={{ p: 1, display: 'flex', justifyContent: 'center', overflow: 'auto' }}>
            <svg
                width={PLOT_SIZE}
                height={PLOT_SIZE}
                viewBox={`0 0 ${PLOT_SIZE} ${PLOT_SIZE}`}
                role="img"
                aria-label="Live radar sky plot"
            >
                <style>{`
                    @keyframes radar-sweep {
                        from { transform: rotate(0deg); }
                        to { transform: rotate(360deg); }
                    }
                `}</style>

                <defs>
                    <radialGradient id="radar-sweep-fade" cx="50%" cy="50%" r="50%">
                        <stop offset="0%" stopColor="#ffffff" stopOpacity="0.18" />
                        <stop offset="100%" stopColor="#ffffff" stopOpacity="0.02" />
                    </radialGradient>
                </defs>

                {/* Plot background */}
                <circle cx={CENTER} cy={CENTER} r={RADIUS} fill="rgba(128,128,128,0.06)" stroke="rgba(128,128,128,0.4)" />

                {/* Elevation rings: 30 / 60 degrees */}
                <circle cx={CENTER} cy={CENTER} r={elToRadius(30)} fill="none" stroke="rgba(128,128,128,0.25)" />
                <circle cx={CENTER} cy={CENTER} r={elToRadius(60)} fill="none" stroke="rgba(128,128,128,0.25)" />

                {/* Cross + diagonal grid */}
                <line x1={CENTER - RADIUS} y1={CENTER} x2={CENTER + RADIUS} y2={CENTER} stroke="rgba(128,128,128,0.25)" />
                <line x1={CENTER} y1={CENTER - RADIUS} x2={CENTER} y2={CENTER + RADIUS} stroke="rgba(128,128,128,0.25)" />
                <line
                    x1={CENTER - RADIUS * Math.SQRT1_2} y1={CENTER - RADIUS * Math.SQRT1_2}
                    x2={CENTER + RADIUS * Math.SQRT1_2} y2={CENTER + RADIUS * Math.SQRT1_2}
                    stroke="rgba(128,128,128,0.12)"
                />
                <line
                    x1={CENTER - RADIUS * Math.SQRT1_2} y1={CENTER + RADIUS * Math.SQRT1_2}
                    x2={CENTER + RADIUS * Math.SQRT1_2} y2={CENTER - RADIUS * Math.SQRT1_2}
                    stroke="rgba(128,128,128,0.12)"
                />

                {/* Cardinal labels */}
                {CARDINALS.map(([label, az]) => {
                    const [x, y] = azToXY(az, RADIUS + 18);
                    return (
                        <text
                            key={label}
                            x={x}
                            y={y}
                            textAnchor="middle"
                            dominantBaseline="central"
                            fontSize={14}
                            fill="rgba(128,128,128,0.9)"
                        >
                            {label}
                        </text>
                    );
                })}

                {/* Animated sweep wedge (~4s per revolution) */}
                <g style={{ transformOrigin: `${CENTER}px ${CENTER}px`, animation: 'radar-sweep 4s linear infinite' }}>
                    <path
                        d={`M ${CENTER} ${CENTER} L ${CENTER} ${CENTER - RADIUS} A ${RADIUS} ${RADIUS} 0 0 1 ${azToXY(24, RADIUS)[0]} ${azToXY(24, RADIUS)[1]} Z`}
                        fill="url(#radar-sweep-fade)"
                    />
                    <line x1={CENTER} y1={CENTER} x2={CENTER} y2={CENTER - RADIUS} stroke="#ffffff" strokeOpacity={0.35} />
                </g>

                {/* Satellite blips */}
                {blips.map((blip) => {
                    const r = elToRadius(blip.el);
                    const [x, y] = azToXY(blip.az, r);
                    const color = blipColor(blip);
                    // Non-monitored blips are labeled on hover only (via the tooltip)
                    const showLabel = Boolean(blip.monitored);
                    return (
                        <Tooltip
                            key={`${blip.norad_id}`}
                            title={<BlipTooltip blip={blip} stationNameById={stationNameById} />}
                            enterDelay={100}
                            arrow
                        >
                            <g style={{ cursor: 'pointer' }}>
                                <circle cx={x} cy={y} r={blip.monitored ? 5 : 4} fill={color} fillOpacity={0.9} />
                                {showLabel && (
                                    <text
                                        x={x + 8}
                                        y={y - 6}
                                        fontSize={10}
                                        fill="rgba(200,200,200,0.95)"
                                        stroke="rgba(0,0,0,0.6)"
                                        strokeWidth={2}
                                        paintOrder="stroke"
                                    >
                                        {blip.name ?? blip.norad_id}
                                    </text>
                                )}
                            </g>
                        </Tooltip>
                    );
                })}
            </svg>
        </Paper>
    );
}

function BlipTooltip({ blip, stationNameById }) {
    const { t } = useTranslation('radar');
    const seenBy = (blip.seenBy ?? []).map((id) => stationNameById[id] ?? id).join(', ');
    return (
        <Box sx={{ fontSize: 12, lineHeight: 1.6 }}>
            <div><strong>{blip.name ?? blip.norad_id}</strong> (NORAD {blip.norad_id})</div>
            <div>{t('azimuth')}: {blip.az != null ? `${Number(blip.az).toFixed(1)}°` : '—'} · {t('elevation')}: {blip.el != null ? `${Number(blip.el).toFixed(1)}°` : '—'}</div>
            <div>{t('range')}: {blip.range_km != null ? `${Number(blip.range_km).toFixed(0)} km` : '—'}</div>
            {blip.downlink_min_mhz != null && (
                <div>
                    {t('downlink')}: {Number(blip.downlink_min_mhz).toFixed(3)}
                    {blip.downlink_max_mhz != null && blip.downlink_max_mhz !== blip.downlink_min_mhz
                        ? `–${Number(blip.downlink_max_mhz).toFixed(3)}` : ''} MHz
                </div>
            )}
            <div>{t('visible_from')}: {seenBy || '—'}</div>
        </Box>
    );
}
