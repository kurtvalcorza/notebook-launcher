from __future__ import annotations

from pathlib import Path

import pytest

from notebook_launcher.state import StateStore


@pytest.fixture
def state_store(tmp_path: Path) -> StateStore:
    store = StateStore(tmp_path / "state.db")
    store.initialize()
    return store
