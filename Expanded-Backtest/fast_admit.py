#!/usr/bin/env python3
"""Numba fast paths for portfolio admission and the singular rule.

Semantics are identical to ``trade_sim.admit_portfolio`` and
``trade_sim.apply_singular``; only the Python-level loops are replaced.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
from numba import njit

ORDER_COLUMNS = ["signal_time", "score", "range_value", "symbol"]
ORDER_ASCENDING = [True, False, False, True]


@njit(cache=True)
def _admit(signal: np.ndarray, exit_ms: np.ndarray, max_open: int) -> np.ndarray:
    """Min-heap of active exit times; accept while fewer than max_open are open."""
    n = signal.shape[0]
    accepted = np.zeros(n, dtype=np.bool_)
    heap = np.empty(n, dtype=np.int64)
    size = 0
    for i in range(n):
        now = signal[i]
        while size > 0 and heap[0] <= now:
            size -= 1
            if size > 0:
                heap[0] = heap[size]
                pos = 0
                while True:
                    left = 2 * pos + 1
                    right = left + 1
                    small = pos
                    if left < size and heap[left] < heap[small]:
                        small = left
                    if right < size and heap[right] < heap[small]:
                        small = right
                    if small == pos:
                        break
                    tmp = heap[pos]
                    heap[pos] = heap[small]
                    heap[small] = tmp
                    pos = small
        if size >= max_open:
            continue
        accepted[i] = True
        pos = size
        heap[pos] = exit_ms[i]
        size += 1
        while pos > 0:
            parent = (pos - 1) // 2
            if heap[parent] <= heap[pos]:
                break
            tmp = heap[parent]
            heap[parent] = heap[pos]
            heap[pos] = tmp
            pos = parent
    return accepted


def admit_portfolio(candidates: pd.DataFrame, max_open: int) -> pd.DataFrame:
    """Chronological N-slot admission for one scenario."""
    if candidates.empty:
        return candidates
    ordered = candidates.sort_values(
        ORDER_COLUMNS,
        ascending=ORDER_ASCENDING,
        na_position="last",
        kind="mergesort",
    )
    signal = ordered["signal_time"].to_numpy(dtype="int64")
    exit_ms = ordered["exit_time"].to_numpy(dtype="int64")
    return ordered[_admit(signal, exit_ms, int(max_open))].copy()


@njit(cache=True)
def _singular(signal: np.ndarray, exit_ms: np.ndarray,
              group: np.ndarray) -> np.ndarray:
    """Accept a trade when the same scenario+symbol has no open position."""
    n = signal.shape[0]
    accepted = np.zeros(n, dtype=np.bool_)
    last_exit = np.int64(-1)
    previous = np.int64(-1)
    for i in range(n):
        code = group[i]
        if code != previous:
            previous = code
            last_exit = np.int64(-1)
        if last_exit < 0 or signal[i] >= last_exit:
            accepted[i] = True
            last_exit = exit_ms[i]
    return accepted


def apply_singular(normal: pd.DataFrame) -> pd.DataFrame:
    """Block a normal trade while the same scenario+symbol is still open."""
    if normal.empty:
        return normal
    labels = normal.index
    normal = normal.reset_index(drop=True)
    ordered = normal.sort_values(
        ["scenario_id", "symbol", "signal_time", "entry_time", "exit_time"],
        kind="mergesort",
    )
    codes = pd.factorize(
        pd.MultiIndex.from_frame(ordered[["scenario_id", "symbol"]]),
        sort=False,
    )[0]
    keep = _singular(
        ordered["signal_time"].to_numpy(dtype="int64"),
        ordered["exit_time"].to_numpy(dtype="int64"),
        codes.astype("int64"),
    )
    mask = np.zeros(len(normal), dtype=np.bool_)
    mask[ordered.index.to_numpy()[keep]] = True
    out = normal[mask].copy()
    out.index = labels[mask]
    return out
