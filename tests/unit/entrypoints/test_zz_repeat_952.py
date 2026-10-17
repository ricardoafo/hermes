import pytest

from tests.unit.entrypoints import test_tui_live_screen as m


@pytest.mark.parametrize("n", range(40))
def test_repeat(n: int, monkeypatch: pytest.MonkeyPatch) -> None:
    m.test_tick_flashes_the_moved_cells(monkeypatch)
    monkeypatch.undo()
    m.test_the_tick_flash_ends(monkeypatch)
