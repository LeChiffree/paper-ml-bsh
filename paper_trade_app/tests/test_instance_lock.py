from __future__ import annotations

import sys
from pathlib import Path

import pytest


APP = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(APP))

from instance_lock import EngineInstanceLock


def test_only_one_engine_can_hold_database_lock(tmp_path):
    path = tmp_path / "paper.engine.lock"
    first = EngineInstanceLock(path)
    second = EngineInstanceLock(path)
    first.acquire()
    try:
        with pytest.raises(RuntimeError, match="Another Paper ML engine"):
            second.acquire()
    finally:
        first.release()
    second.acquire()
    second.release()
