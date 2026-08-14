from core.events import FATAL_CRASH, FATAL_PORT_IN_USE, ClientCount, Fatal, Ready
from core.status import CONNECTED, ERROR, LISTENING, PORT_IN_USE, STARTING, StatusModel


def _ready(port=8765):
    return Ready(ips=("192.168.1.42",), port=port, token="abc",
                 cert_fp="A1:B2", pairing_uri="starcompanion://pair")


def test_starts_in_the_starting_state():
    model = StatusModel()
    assert model.status == STARTING
    assert model.text == "Starting…"
    assert model.color == "#6b7785"
    assert model.fatal is False


def test_ready_moves_to_listening():
    model = StatusModel()
    model.apply(_ready())
    assert model.status == LISTENING
    assert model.text == "Listening — waiting for app"
    assert model.color == "#4aa8ff"


def test_one_client_moves_to_connected_and_is_singular():
    model = StatusModel()
    model.apply(_ready())
    model.apply(ClientCount(1))
    assert model.status == CONNECTED
    assert model.text == "Connected — 1 app"
    assert model.color == "#4ade80"


def test_several_clients_pluralise():
    model = StatusModel()
    model.apply(_ready())
    model.apply(ClientCount(3))
    assert model.text == "Connected — 3 apps"


def test_last_client_leaving_returns_to_listening():
    model = StatusModel()
    model.apply(_ready())
    model.apply(ClientCount(1))
    model.apply(ClientCount(0))
    assert model.status == LISTENING
    assert model.text == "Listening — waiting for app"


def test_port_in_use_names_the_port_it_tried():
    model = StatusModel()
    model.apply(Fatal(kind=FATAL_PORT_IN_USE, message="port 8765 is already in use"))
    assert model.status == PORT_IN_USE
    assert model.text == "Port 8765 in use — is the bridge already running?"
    assert model.color == "#f87171"
    assert model.fatal is True


def test_port_in_use_falls_back_when_the_port_cannot_be_read():
    model = StatusModel()
    model.apply(Fatal(kind=FATAL_PORT_IN_USE, message="address in use"))
    assert model.text == "Port in use — is the bridge already running?"


def test_port_in_use_prefers_the_port_already_seen_from_ready():
    model = StatusModel()
    model.apply(_ready(port=9000))
    model.apply(Fatal(kind=FATAL_PORT_IN_USE, message="address in use"))
    assert model.text == "Port 9000 in use — is the bridge already running?"


def test_a_crash_reports_error():
    model = StatusModel()
    model.apply(Fatal(kind=FATAL_CRASH, message="boom"))
    assert model.status == ERROR
    assert model.text == "Error — see activity log"
    assert model.fatal is True


def test_fatal_latches_until_reset():
    model = StatusModel()
    model.apply(Fatal(kind=FATAL_CRASH, message="boom"))
    model.apply(_ready())
    model.apply(ClientCount(2))
    assert model.status == ERROR  # a dead thread's late events cannot clear it
    model.reset()
    assert model.status == STARTING
    assert model.fatal is False
    model.apply(_ready())
    assert model.status == LISTENING


def test_reset_clears_the_client_count():
    model = StatusModel()
    model.apply(_ready())
    model.apply(ClientCount(2))
    model.reset()
    assert model.clients == 0


def test_unrelated_events_are_ignored():
    from core.events import TelemetryState

    model = StatusModel()
    model.apply(_ready())
    model.apply(TelemetryState(found=True, path="F:/Game.log", feeds=2))
    assert model.status == LISTENING
