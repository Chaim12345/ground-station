export const formatLoraConfig = (config) => {
    if (!config || typeof config !== 'object') return '-';

    const parts = [];
    if (config.sf != null) parts.push(`SF${config.sf}`);
    if (config.bw != null) parts.push(`BW${config.bw / 1000}k`);
    if (config.cr != null) parts.push(`CR4/${config.cr + 4}`);
    if (Array.isArray(config.sync_word)) {
        if (config.sync_word.length === 1) {
            parts.push(`SW 0x${config.sync_word[0].toString(16).padStart(2, '0').toUpperCase()}`);
        } else if (config.sync_word.length === 2) {
            parts.push(config.sync_word.every(value => value === 0)
                ? 'Any sync word'
                : `SW [${config.sync_word.join(', ')}]`);
        }
    }
    if (config.preamble_len != null) parts.push(`Preamble ${config.preamble_len}`);
    if (config.ldro_mode != null) {
        const label = {0: 'Off', 1: 'On', 2: 'Auto'}[config.ldro_mode] ?? config.ldro_mode;
        parts.push(`LDRO ${label}`);
    }
    if (config.implicit_header != null) {
        parts.push(config.implicit_header ? 'Implicit header' : 'Explicit header');
    }
    if (config.payload_length != null) {
        parts.push(`${config.payload_length} payload bytes`);
    }
    if (config.has_crc != null) {
        parts.push(config.has_crc ? 'CRC' : 'No CRC');
    }
    if (config.invert_iq != null) parts.push(config.invert_iq ? 'Inverted IQ' : 'Normal IQ');
    return parts.join(' · ') || '-';
};
