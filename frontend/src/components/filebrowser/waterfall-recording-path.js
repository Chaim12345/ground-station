/**
 * Build the task path from the recording's storage location. Observation
 * details from older servers omit observation_bundle but retain the data URL.
 */
export function getWaterfallRecordingPath(recording) {
    const name = recording.name || recording.data_file;
    if (!name) throw new Error('Recording name is missing');
    const baseName = name.replace(/\.(sigmf-data|sigmf-meta)$/, '');
    const dataUrl = recording.download_urls?.data || '';
    const bundleUrlMatch = /^\/observations\/([^/]+)\/recordings\/[^/]+\.sigmf-data(?:\?.*)?$/.exec(dataUrl);
    if (dataUrl.startsWith('/observations/') && !bundleUrlMatch) {
        throw new Error('Cannot determine observation recording path');
    }
    const observationBundle = recording.observation_bundle
        || (bundleUrlMatch ? decodeURIComponent(bundleUrlMatch[1]) : null);

    return observationBundle
        ? `observations/${observationBundle}/recordings/${baseName}`
        : `/recordings/${baseName}`;
}
