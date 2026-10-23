import { getWaterfallRecordingPath } from './waterfall-recording-path.js';

function normalizeRecordingPath(path) {
    if (typeof path !== 'string') return null;
    const normalized = path.replace(/\\/g, '/').replace(/\/+$/, '').replace(/\.(sigmf-data|sigmf-meta)$/, '');
    // Tasks started by the recorder can carry absolute filesystem paths,
    // while File Browser starts them with paths relative to the data root.
    const observation = /(?:^|\/)(observations\/[^/]+\/recordings\/[^/]+)$/.exec(normalized);
    if (observation) return observation[1];
    const standalone = /(?:^|\/)(recordings\/[^/]+)$/.exec(normalized);
    return standalone?.[1] || null;
}

export function getRunningWaterfallPaths(backgroundTasks) {
    const tasks = backgroundTasks?.tasks || {};
    const runningIds = backgroundTasks?.runningTaskIds || [];
    const paths = new Set();
    for (const id of runningIds) {
        const task = tasks[id];
        if (task?.status !== 'running' || task.command !== 'generate_waterfall_task') continue;
        const path = normalizeRecordingPath(task.args?.[0]);
        if (path) paths.add(path);
    }
    return paths;
}

export function isWaterfallRunningForItem(item, runningPaths) {
    if (!item || !runningPaths?.size) return false;
    if (item.type === 'recording') {
        try {
            return runningPaths.has(normalizeRecordingPath(getWaterfallRecordingPath(item)));
        } catch (_) {
            return false;
        }
    }
    if (item.folder_kind === 'observation' && item.foldername) {
        const prefix = `observations/${item.foldername}/recordings/`;
        for (const path of runningPaths) {
            if (path.startsWith(prefix)) return true;
        }
    }
    return false;
}
