import pytest


@pytest.fixture
def tk_root():
    """A hidden Tk root, or a skip when there is no display.

    CI and the macOS dev machine may both lack one; the logic these widgets
    wrap is tested separately so a skip here costs little.
    """
    tkinter = pytest.importorskip("tkinter")
    try:
        root = tkinter.Tk()
    except Exception as exc:  # noqa: BLE001 - any display failure is a skip
        pytest.skip(f"no display available: {exc}")
    root.withdraw()
    yield root
    root.destroy()
