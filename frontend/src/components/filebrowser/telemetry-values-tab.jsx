/**
 * @license
 * Copyright (c) 2025 Efstratios Goudelis
 */

import React from 'react';
import { Alert, Box, Typography, Divider } from '@mui/material';
import { useTranslation } from 'react-i18next';

function Row({ label, value }) {
  return (
    <Box sx={{
      display: 'grid',
      gridTemplateColumns: '1fr 1fr',
      alignItems: 'center',
      py: 0.75,
      columnGap: 2,
    }}>
      <Typography variant="body2" color="text.secondary">{label}</Typography>
      <Typography variant="body2" sx={{ fontFamily: 'monospace' }}>{value}</Typography>
    </Box>
  );
}

function Values({ values, fieldInfo = {} }) {
  const { t } = useTranslation('filebrowser');
  const formatValue = (value) => {
    if (value === null || value === undefined) {
      return t('telemetry_values_tab.unavailable', { defaultValue: 'Unavailable' });
    }
    if (typeof value === 'boolean') {
      return value
        ? t('telemetry_values_tab.yes', { defaultValue: 'Yes' })
        : t('telemetry_values_tab.no', { defaultValue: 'No' });
    }
    if (typeof value === 'number') {
      return new Intl.NumberFormat(undefined, { maximumFractionDigits: 6 }).format(value);
    }
    return typeof value === 'object' ? JSON.stringify(value) : String(value);
  };

  return Object.entries(values).map(([key, value]) => {
    const info = fieldInfo[key] || {};
    // Missing/invalid sensor readings must not look like a measured zero.
    const unit = value != null && info.unit ? ` ${info.unit}` : '';
    return <Row key={key} label={info.label || key} value={`${formatValue(value)}${unit}`} />;
  });
}

export default function TelemetryValuesTab({ telemetry }) {
  const { t } = useTranslation('filebrowser');
  const values = telemetry?.telemetry?.values || null;
  const rawFields = telemetry?.telemetry?.raw_fields || telemetry?.telemetry?.rawFields || null;
  const payload = telemetry?.telemetry;

  // A USP AX.25 frame can carry several independent subsystem messages. Keep
  // their values together so repeated fields (time, current, etc.) stay unambiguous.
  if (payload?.format === 'usp' && Array.isArray(payload.messages)) {
    return (
      <Box>
        {payload.error && <Alert severity="warning" sx={{ mb: 2 }}>{payload.error}</Alert>}
        {payload.messages.map((message, index) => (
          <Box key={`${index}-${message.message_id}`} component="section" sx={{ mb: 3 }}>
            <Typography variant="subtitle2" sx={{ fontWeight: 700 }}>{message.name}</Typography>
            <Typography variant="caption" color="text.secondary">
              {t('telemetry_values_tab.usp_message', {
                defaultValue: 'USP {{id}} · Device {{source}} → {{destination}} · {{length}} bytes',
                id: message.message_id,
                source: message.source,
                destination: message.destination,
                length: message.length,
              })}
            </Typography>
            <Divider sx={{ my: 1 }} />
            {message.error ? (
              <Alert severity="warning">{message.error}</Alert>
            ) : message.status === 'decoded' ? (
              <Values values={message.values} fieldInfo={message.field_info} />
            ) : (
              <Typography variant="body2" color="text.secondary">
                {message.status === 'non_telemetry'
                  ? t('telemetry_values_tab.non_telemetry_message', { defaultValue: 'This message carries no satellite telemetry. Its bytes are available in Hex + ASCII.' })
                  : t('telemetry_values_tab.unsupported_message', { defaultValue: 'This message type is not decoded yet. Its bytes are available in Hex + ASCII.' })}
              </Typography>
            )}
            {Object.keys(message.raw_fields || {}).length > 0 && (
              <Box component="details" sx={{ mt: 2 }}>
                <Typography component="summary" variant="body2" sx={{ cursor: 'pointer' }}>
                  {t('telemetry_values_tab.raw_fields', { defaultValue: 'Raw Fields' })}
                </Typography>
                {Object.entries(message.raw_fields).map(([key, value]) => (
                  <Row key={key} label={key} value={String(value)} />
                ))}
              </Box>
            )}
          </Box>
        ))}
      </Box>
    );
  }

  if (!values) {
    return (
      <Typography variant="body2" color="text.secondary">
        {t('telemetry_values_tab.no_decoded_telemetry_available_for_this_packet', { defaultValue: 'No decoded telemetry available for this packet.' })}
      </Typography>
    );
  }

  return (
    <Box>
      <Typography variant="subtitle2" sx={{ fontWeight: 700, mb: 1 }}>{t('telemetry_values_tab.engineering_values', { defaultValue: 'Engineering Values' })}</Typography>
      <Divider sx={{ mb: 2 }} />
      <Box>
        <Values values={values} fieldInfo={payload?.field_info} />
      </Box>

      {rawFields && (
        <Box sx={{ mt: 3 }}>
          <Typography variant="subtitle2" sx={{ fontWeight: 700, mb: 1 }}>{t('telemetry_values_tab.raw_fields', { defaultValue: 'Raw Fields' })}</Typography>
          <Divider sx={{ mb: 2 }} />
          {Object.entries(rawFields).map(([k, v]) => (
            <Row key={k} label={k} value={String(v)} />
          ))}
        </Box>
      )}
    </Box>
  );
}
