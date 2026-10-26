"""spyserver_worker_process: reconnect keeps timing and re-verifies tuning."""

import queue
import threading
import time

import numpy as np
import pytest

from workers import spyserverworker as sw


class FakeClient:
    """Stands in for SpyServerClient; scripted per connection."""

    instances: list = []
    script: list = []  # per-connection dicts: {"blocks": n, "tuned": bool}

    def __init__(self, hostname, port):
        self.index = len(FakeClient.instances)
        FakeClient.instances.append(self)
        plan = FakeClient.script[min(self.index, len(FakeClient.script) - 1)]
        self.blocks_left = plan["blocks"]
        self.tuned = plan.get("tuned", True)
        self.rate = 48_000.0
        self.center = None
        self.stream_stats = {"gaps": 0}
        self.device_type_name = "Fake"

    def connect(self):
        return True

    @property
    def sample_rate(self):
        return self.rate

    @sample_rate.setter
    def sample_rate(self, value):
        pass

    @property
    def center_freq(self):
        return self.center

    @center_freq.setter
    def center_freq(self, value):
        self.center = value

    @property
    def gain(self):
        return 0

    @gain.setter
    def gain(self, value):
        pass

    def set_agc_mode(self, enable):
        pass

    def start_stream(self):
        pass

    def wait_for_tuning(self, timeout=5.0):
        return {
            "ok": self.tuned,
            "reason": "" if self.tuned else "someone else is listening",
            "can_control": 1,
            "device_center": self.center,
            "iq_center": self.center if self.tuned else 1,
            "requested": self.center,
        }

    def read_samples(self, n, timeout=10.0):
        if self.blocks_left <= 0:
            raise ConnectionError("script exhausted")
        self.blocks_left -= 1
        if self.blocks_left == 0 and self.index == 0:
            # first connection dies after its blocks
            self.read_samples = self._fail
        # Non-constant so DC removal leaves non-zero samples (zero = filler).
        return np.exp(1j * np.arange(n) * 0.3).astype(np.complex64)

    def _fail(self, n, timeout=10.0):
        raise ConnectionError("Connection reset by peer")

    def close(self):
        pass


def _run_worker(monkeypatch, script, runtime=3.0):
    FakeClient.instances = []
    FakeClient.script = script
    monkeypatch.setattr(sw, "SpyServerClient", FakeClient)
    # reconnect waits 3 s * attempt in production; make the test fast but
    # leave a measurable outage.
    real_wait = threading.Event.wait

    config_q: queue.Queue = queue.Queue()
    data_q: queue.Queue = queue.Queue()
    demod_q: queue.Queue = queue.Queue(maxsize=100000)
    stop = threading.Event()
    monkeypatch.setattr(
        threading.Event, "wait", lambda self, t=None: real_wait(self, min(t or 0, 0.2))
    )
    config_q.put(
        {
            "sdr_id": "s",
            "client_id": "c",
            "host": "h",
            "port": 1,
            "center_freq": 145_935_000,
            "sample_rate": 48_000,
            "fft_size": 4096,
        }
    )
    t = threading.Thread(
        target=sw.spyserver_worker_process,
        args=(config_q, data_q, stop, None, demod_q),
        daemon=True,
    )
    t.start()
    time.sleep(runtime)
    stop.set()
    t.join(5)
    msgs = []
    while not demod_q.empty():
        msgs.append(demod_q.get())
    events = []
    while not data_q.empty():
        events.append(data_q.get())
    return msgs, events


def test_reconnect_zero_fills_outage_and_keeps_indices_continuous(monkeypatch):
    msgs, events = _run_worker(monkeypatch, [{"blocks": 5}, {"blocks": 10_000}], runtime=2.0)
    assert len(FakeClient.instances) >= 2  # reconnected
    # chunk indices / sample positions are continuous across the outage
    starts = [m["stream_start_sample"] for m in msgs]
    counts = [m["stream_sample_count"] for m in msgs]
    assert all(s2 == s1 + c1 for s1, c1, s2 in zip(starts, counts, starts[1:]))
    assert [m["stream_chunk_id"] for m in msgs] == list(range(len(msgs)))
    # zero-filled samples were inserted for the outage
    zero_chunks = [m for m in msgs if not np.any(m["samples"])]
    assert zero_chunks, "outage was not filled"
    stats = [e["stats"] for e in events if e.get("type") == "stats"]
    assert stats and stats[-1]["reconnects"] >= 1
    assert stats[-1]["outage_fill_samples"] > 0


def test_reconnect_rejected_when_server_no_longer_tuned(monkeypatch):
    msgs, events = _run_worker(monkeypatch, [{"blocks": 5}, {"blocks": 10, "tuned": False}], runtime=2.0)
    # every reconnect attempt is rejected -> worker reports an error and stops
    assert any(e.get("type") == "error" for e in events)
    # only the 5 real blocks were published: nothing from the mistuned server,
    # and no outage padding for a reconnect that never succeeded
    assert len(msgs) == 5
    assert all(np.any(m["samples"]) for m in msgs)


@pytest.mark.parametrize("tuned", [False])
def test_initial_mistune_fails_loudly(monkeypatch, tuned):
    msgs, events = _run_worker(monkeypatch, [{"blocks": 10, "tuned": tuned}], runtime=1.0)
    assert msgs == []
    errors = [e for e in events if e.get("type") == "error"]
    assert errors and "not tuned" in errors[0]["message"]
