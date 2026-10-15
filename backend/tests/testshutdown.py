"""Shutdown cleanup for workers left after client sessions are gone."""

import pytest

from server import shutdown


@pytest.mark.asyncio
async def test_shutdown_stops_sdr_worker_retained_by_joiner(monkeypatch):
    class ProcessManager:
        def __init__(self):
            self.processes = {"sdr-1": {"clients": {"joiner"}}}
            self.stopped = []

        async def stop_sdr_process(self, sdr_id):
            self.stopped.append(sdr_id)
            self.processes.pop(sdr_id)

    process_manager = ProcessManager()
    monkeypatch.setattr(shutdown.runtimestate, "process_manager", process_manager)
    monkeypatch.setattr(shutdown, "active_sdr_clients", {})

    await shutdown.cleanup_sessions()
    await shutdown.cleanup_sdr_processes()

    assert process_manager.stopped == ["sdr-1"]
    assert process_manager.processes == {}
