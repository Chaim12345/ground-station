"""Exercise LoRa controls through the live VFO handler and config resolver."""

import logging

import pytest

from handlers.entities import vfo as handlers
from pipeline.config.decoderconfigservice import DecoderConfigService
from vfos.state import VFOManager

SESSION = "lora-vfo-test"
LOGGER = logging.getLogger(__name__)
PARAMETERS = {
    "sf": 8,
    "bw": 125000,
    "cr": 1,
    "sync_word": [52],
    "preamble_len": 12,
    "ldro_mode": 0,
    "implicit_header": True,
    "payload_length": 107,
    "has_crc": False,
    "invert_iq": False,
}


class Receiver:
    pass


class Processes:
    def __init__(self):
        self.starts = []
        self.processes = {"playback": {"data_queue": object(), "decoders": {SESSION: {}}}}

    def get_active_decoder(self, sdr_id, session_id, vfo):
        entry = self.processes[sdr_id]["decoders"][session_id].get(vfo)
        return entry["instance"] if entry else None

    def start_decoder(self, **kwargs):
        config = DecoderConfigService().get_config(
            "lora",
            satellite=kwargs.get("satellite"),
            transmitter=kwargs.get("transmitter"),
            overrides=kwargs["decoder_param_overrides"],
        )
        self.starts.append(config)
        self.processes["playback"]["decoders"][SESSION][2] = {
            "instance": Receiver(),
            "config": config,
        }
        return True

    def stop_decoder(self, sdr_id, session_id, vfo):
        self.processes[sdr_id]["decoders"][session_id].pop(vfo, None)


@pytest.fixture
def processes(monkeypatch):
    processes = Processes()
    manager = VFOManager()
    monkeypatch.setattr(manager, "_session_vfo_states", {})
    monkeypatch.setattr(handlers, "_decoder_param_overrides_cache", {})
    monkeypatch.setattr(handlers, "process_manager", processes)
    monkeypatch.setattr(handlers.session_tracker, "get_session_sdr", lambda _sid: "playback")
    monkeypatch.setattr(handlers.decoder_registry, "get_decoder_class", lambda _name: Receiver)
    manager.update_vfo_state(SESSION, 2, center_freq=436100000, locked_transmitter_id="")
    return processes


async def apply(**updates):
    response = await handlers.update_vfo_parameters(
        None, {"vfoNumber": 2, **updates}, LOGGER, SESSION
    )
    assert response["success"] is True


@pytest.mark.unit
async def test_live_lora_controls_reach_initial_decoder_config(processes):
    await apply(active=True, decoder="lora", parametersEnabled=True, **PARAMETERS)

    assert len(processes.starts) == 1
    config = processes.starts[0].to_dict()
    assert {key: config[key] for key in PARAMETERS} == PARAMETERS


@pytest.mark.unit
async def test_changing_lora_controls_restarts_with_new_phy_settings(processes):
    await apply(active=True, decoder="lora", parametersEnabled=True)
    assert processes.starts[-1].sf == 7

    # This is the dialog's Apply request, without a decoder/active transition.
    await apply(**PARAMETERS)
    assert len(processes.starts) == 2
    assert processes.starts[-1].sf == 8
    assert processes.starts[-1].sync_word == [52]
    assert processes.starts[-1].has_crc is False
    assert processes.starts[-1].ldro_mode == 0

    # Tuning alone and reapplying identical parameters must retain acquisition.
    await apply(frequency=436100100)
    await apply(**PARAMETERS)
    assert len(processes.starts) == 2

    await apply(**{**PARAMETERS, "sync_word": [18], "invert_iq": True})
    assert len(processes.starts) == 3
    assert processes.starts[-1].sync_word == [18]
    assert processes.starts[-1].invert_iq is True


@pytest.mark.unit
async def test_disabling_and_reenabling_lora_overrides(processes):
    await apply(active=True, decoder="lora", parametersEnabled=True, **PARAMETERS)
    await apply(parametersEnabled=False)
    assert processes.starts[-1].sf == 7
    assert processes.starts[-1].sync_word == [18]

    await apply(parametersEnabled=True, **PARAMETERS)
    assert processes.starts[-1].sf == 8
    assert processes.starts[-1].sync_word == [52]


@pytest.mark.unit
async def test_legacy_forced_ldro_reaches_lora_decoder(processes):
    await apply(active=True, decoder="lora", parametersEnabled=True, sf=8, fldro=True)
    assert processes.starts[-1].ldro_mode == 1
