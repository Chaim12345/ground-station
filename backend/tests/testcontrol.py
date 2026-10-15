# Copyright (c) 2026 Efstratios Goudelis

import asyncio
from uuid import uuid4

import pytest

from common.events import EventBroker
from handlers.entities import control
from server import startup


class _Sio:
    async def emit(self, *args, **kwargs):
        del args, kwargs


class _Logger:
    def info(self, *args, **kwargs):
        del args, kwargs


@pytest.mark.asyncio
async def test_restart_closes_public_event_stream_before_shutdown(monkeypatch):
    broker = EventBroker()
    subscriber = broker.subscribe("token-id", ["artifacts:read"])
    monkeypatch.setattr(control, "public_events", broker)
    monkeypatch.setattr(startup, "public_events", broker)

    class DeferredThread:
        def __init__(self, target, daemon):
            del target, daemon

        def start(self):
            pass

    monkeypatch.setattr(control.threading, "Thread", DeferredThread)

    stream = startup._stream_api_events("unused-token", "token-id", subscriber)
    try:
        assert await anext(stream) == "retry: 3000\n\n"
        assert "event: connected" in await anext(stream)
        for index in range(32):
            broker.artifact_ready(f"artifact-{index}", "snapshot", f"snapshot-{index}.png")

        reply = await control.restart_service(_Sio(), None, _Logger(), "admin-sid")

        assert reply["success"] is True
        with pytest.raises(StopAsyncIteration):
            await asyncio.wait_for(anext(stream), timeout=1)
        with pytest.raises(RuntimeError, match="service restart"):
            broker.subscribe("new-token", ["artifacts:read"])
    finally:
        await stream.aclose()


@pytest.mark.asyncio
async def test_full_backup_can_be_cancelled_by_its_owner(monkeypatch):
    """A cancellation RPC stops the matching in-flight export request."""
    started = asyncio.Event()

    async def _slow_full_backup(progress_callback=None):
        del progress_callback
        started.set()
        await asyncio.Event().wait()
        return {"success": True, "sql": "unreachable"}

    control._FULL_BACKUP_OPERATIONS.clear()
    monkeypatch.setattr(control, "full_backup", _slow_full_backup)
    operation_id = str(uuid4())
    backup_task = asyncio.create_task(
        control.backup_full_dump(_Sio(), {"operation_id": operation_id}, _Logger(), "owner-sid")
    )
    await started.wait()

    cancel_reply = await control.cancel_full_backup(
        _Sio(), {"operation_id": operation_id}, _Logger(), "owner-sid"
    )
    backup_reply = await backup_task

    assert cancel_reply == {
        "success": True,
        "operation_id": operation_id,
        "cancelling": True,
    }
    assert backup_reply == {"success": True, "cancelled": True}
    assert control._FULL_BACKUP_OPERATIONS == {}


@pytest.mark.asyncio
async def test_full_backup_cannot_be_cancelled_by_another_socket(monkeypatch):
    """Operation IDs are scoped to their initiating Socket.IO session."""
    started = asyncio.Event()

    async def _slow_full_backup(progress_callback=None):
        del progress_callback
        started.set()
        await asyncio.Event().wait()
        return {"success": True, "sql": "unreachable"}

    control._FULL_BACKUP_OPERATIONS.clear()
    monkeypatch.setattr(control, "full_backup", _slow_full_backup)
    operation_id = str(uuid4())
    backup_task = asyncio.create_task(
        control.backup_full_dump(_Sio(), {"operation_id": operation_id}, _Logger(), "owner-sid")
    )
    await started.wait()

    cancel_reply = await control.cancel_full_backup(
        _Sio(), {"operation_id": operation_id}, _Logger(), "other-sid"
    )
    assert cancel_reply["success"] is False

    backup_task.cancel()
    backup_reply = await backup_task
    assert backup_reply == {"success": True, "cancelled": True}
