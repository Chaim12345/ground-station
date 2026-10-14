import { useEffect, useId, useState } from 'react';
import { Alert, FormControl, InputLabel, MenuItem, Select, Stack } from '@mui/material';
import { useTranslation } from 'react-i18next';

export const libreLanguageCode = (code) => code === 'zh' ? 'zh-Hans' : code;

export const isLibrePairSupported = (capabilities, source, target) => {
    if (!capabilities?.configured || !capabilities.languages?.length) return false;
    const targetCode = libreLanguageCode(target);
    const sources = source === 'auto'
        ? capabilities.languages
        : capabilities.languages.filter((language) => language.code === libreLanguageCode(source));
    return sources.some((language) => language.targets?.includes(targetCode));
};

export const getLibreTargetOptions = (options, capabilities, selected) => {
    const known = new Set(options.map((option) => libreLanguageCode(option.value)));
    const extra = (capabilities?.languages || [])
        .filter((language) => !known.has(language.code))
        .map((language) => ({ value: language.code, icon: '🌐', label: language.name }));
    if (selected && !options.some((option) => option.value === selected) && !extra.some((option) => option.value === selected)) {
        extra.push({ value: selected, icon: '🌐', label: selected });
    }
    return [...options, ...extra];
};

export const useLibreTranslateCapabilities = (socket, enabled) => {
    const [capabilities, setCapabilities] = useState(null);
    useEffect(() => {
        if (!socket || !enabled) return undefined;
        let active = true;
        socket.emit('api.call', { cmd: 'get-libretranslate-capabilities', data: {} }, (response) => {
            if (!active) return;
            setCapabilities(response?.success
                ? response.data
                : { configured: false, languages: [], error: response?.error || 'Unavailable' });
        });
        return () => { active = false; };
    }, [socket, enabled]);
    return capabilities;
};

export const TranslationBackendField = ({ value, onChange, source, target, capabilities, disabled = false }) => {
    const { t } = useTranslation('common');
    const labelId = useId();
    const isLocal = value === 'libretranslate';
    return (
        <Stack spacing={1}>
            <FormControl fullWidth size="small" disabled={disabled}>
                <InputLabel id={labelId}>{t('translation.backend_label', { defaultValue: 'Translation Service' })}</InputLabel>
                <Select labelId={labelId} value={value || 'google'} label={t('translation.backend_label', { defaultValue: 'Translation Service' })} onChange={(event) => onChange(event.target.value)}>
                    <MenuItem value="google">Google Translate</MenuItem>
                    <MenuItem value="libretranslate" disabled={capabilities?.configured === false}>LibreTranslate</MenuItem>
                </Select>
            </FormControl>
            {isLocal && target !== 'none' && capabilities && !capabilities.configured && (
                <Alert severity="warning">
                    {t('translation.configure_local', { defaultValue: 'Set the LibreTranslate URL in Application Settings to enable local translation.' })}
                </Alert>
            )}
            {isLocal && target !== 'none' && capabilities?.error && (
                <Alert severity="warning">
                    {t('translation.unavailable', { defaultValue: 'LibreTranslate is unavailable. Check the service URL and connection.' })}
                </Alert>
            )}
            {isLocal && target && target !== 'none' && capabilities?.languages?.length > 0 &&
                !isLibrePairSupported(capabilities, source || 'auto', target) && (
                <Alert severity="warning">
                    {t('translation.unsupported_pair', { defaultValue: 'This language pair is not supported by the configured LibreTranslate service.' })}
                </Alert>
            )}
        </Stack>
    );
};
