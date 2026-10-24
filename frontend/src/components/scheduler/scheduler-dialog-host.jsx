import React from 'react';
import { useSelector } from 'react-redux';
import ObservationFormDialog from './observation-form-dialog.jsx';
import MonitoredSatelliteDialog from './monitored-satellite-dialog.jsx';

const MemoizedObservationFormDialog = React.memo(ObservationFormDialog);
const MemoizedMonitoredSatelliteDialog = React.memo(MonitoredSatelliteDialog);

function DialogSlot({ open, Component }) {
    const [closing, setClosing] = React.useState(false);
    React.useEffect(() => {
        if (open) setClosing(true);
    }, [open]);
    const handleExited = React.useCallback(() => {
        if (!open) setClosing(false);
    }, [open]);

    // Let MUI finish its exit transition before discarding the form state.
    return open || closing ? <Component onExited={handleExited} /> : null;
}

// These forms are available from every page, but their large render trees only
// need to exist while the corresponding dialog is open.
const SchedulerDialogHost = React.memo(function SchedulerDialogHost() {
    const observationOpen = useSelector((state) => Boolean(state.scheduler?.dialogOpen));
    const monitoredOpen = useSelector((state) => Boolean(state.scheduler?.monitoredSatelliteDialogOpen));

    return (
        <>
            <DialogSlot open={observationOpen} Component={MemoizedObservationFormDialog} />
            <DialogSlot open={monitoredOpen} Component={MemoizedMonitoredSatelliteDialog} />
        </>
    );
});

export default SchedulerDialogHost;
