#!/usr/bin/env python3
"""Trade simulation and portfolio admission for the expanded backtest."""
from __future__ import annotations

import heapq
from dataclasses import dataclass

import numpy as np
import pandas as pd
from numba import njit, prange

from data import (
    FEE_RATE,
    FUNDING_PER_8H,
    INITIAL_TL,
    MAX_OPEN,
    SLIPPAGE_4H,
    STAKE_TL,
)

OUTCOME_TP = 0
OUTCOME_SL = 1
OUTCOME_TIME = 2
OUTCOME_INVALID = 3


@njit(parallel=True, cache=True, fastmath=True)
def simulate_exits(
    entry_idx: np.ndarray,
    tp_price: np.ndarray,
    sl_price: np.ndarray,
    deadline_ms: np.ndarray,
    m_open_time: np.ndarray,
    m_high: np.ndarray,
    m_low: np.ndarray,
    m_close: np.ndarray,
    out_time: np.ndarray,
    out_price: np.ndarray,
    out_code: np.ndarray,
) -> None:
    """Scan 1m bars for the first TP/SL touch, otherwise time out."""
    count = entry_idx.shape[0]
    bars = m_open_time.shape[0]
    for j in prange(count):
        i = entry_idx[j]
        if i < 0 or i >= bars:
            out_code[j] = OUTCOME_INVALID
            out_time[j] = 0
            out_price[j] = np.nan
            continue
        tp = tp_price[j]
        sl = sl_price[j]
        deadline = deadline_ms[j]
        last = i
        closed = False
        while i < bars and m_open_time[i] < deadline:
            high = m_high[i]
            low = m_low[i]
            if high >= tp and low <= sl:
                # Stop-first when both barriers are touched by the same bar.
                out_code[j] = OUTCOME_SL
                out_time[j] = m_open_time[i] + 60_000
                out_price[j] = sl
                closed = True
                break
            if high >= tp:
                out_code[j] = OUTCOME_TP
                out_time[j] = m_open_time[i] + 60_000
                out_price[j] = tp
                closed = True
                break
            if low <= sl:
                out_code[j] = OUTCOME_SL
                out_time[j] = m_open_time[i] + 60_000
                out_price[j] = sl
                closed = True
                break
            last = i
            i += 1
        if not closed:
            out_code[j] = OUTCOME_TIME
            out_time[j] = deadline
            out_price[j] = m_close[last]


@dataclass
class CandidateBatch:
    scenario_id: np.ndarray
    signal_time: np.ndarray
    score: np.ndarray
    range_value: np.ndarray
    vol_value: np.ndarray
    signal_close: np.ndarray
    entry_time: np.ndarray
    entry_price: np.ndarray
    tp_price: np.ndarray
    sl_price: np.ndarray
    deadline_ms: np.ndarray
    entry_idx: np.ndarray


def empty_batch() -> CandidateBatch:
    z = np.empty(0, dtype="int64")
    f = np.empty(0, dtype="float64")
    return CandidateBatch(z, z, f, f, f, f, z, f, f, f, z, z)


def concat_batches(items: list[CandidateBatch]) -> CandidateBatch:
    items = [item for item in items if len(item.scenario_id)]
    if not items:
        return empty_batch()
    return CandidateBatch(
        np.concatenate([x.scenario_id for x in items]),
        np.concatenate([x.signal_time for x in items]),
        np.concatenate([x.score for x in items]),
        np.concatenate([x.range_value for x in items]),
        np.concatenate([x.vol_value for x in items]),
        np.concatenate([x.signal_close for x in items]),
        np.concatenate([x.entry_time for x in items]),
        np.concatenate([x.entry_price for x in items]),
        np.concatenate([x.tp_price for x in items]),
        np.concatenate([x.sl_price for x in items]),
        np.concatenate([x.deadline_ms for x in items]),
        np.concatenate([x.entry_idx for x in items]),
    )


def run_exits(
    batch: CandidateBatch,
    one_min: pd.DataFrame,
    symbol: str,
    timeframe: str,
) -> pd.DataFrame:
    if not len(batch.scenario_id):
        return pd.DataFrame()
    opens = one_min["open_time"].to_numpy(dtype="int64")
    high = one_min["high"].to_numpy(dtype="float64")
    low = one_min["low"].to_numpy(dtype="float64")
    close = one_min["close"].to_numpy(dtype="float64")
    count = len(batch.scenario_id)
    out_time = np.empty(count, dtype="int64")
    out_price = np.empty(count, dtype="float64")
    out_code = np.empty(count, dtype="int8")
    simulate_exits(
        batch.entry_idx, batch.tp_price, batch.sl_price, batch.deadline_ms,
        opens, high, low, close, out_time, out_price, out_code,
    )
    valid = out_code != OUTCOME_INVALID
    frame = pd.DataFrame({
        "scenario_id": batch.scenario_id[valid].astype("int16"),
        "symbol": symbol,
        "signal_time": batch.signal_time[valid].astype("int64"),
        "score": batch.score[valid].astype("float32"),
        "range_value": batch.range_value[valid].astype("float32"),
        "vol_value": batch.vol_value[valid].astype("float32"),
        "signal_close": batch.signal_close[valid].astype("float64"),
        "entry_time": batch.entry_time[valid].astype("int64"),
        "entry_price": batch.entry_price[valid].astype("float64"),
        "tp_price": batch.tp_price[valid].astype("float64"),
        "sl_price": batch.sl_price[valid].astype("float64"),
        "exit_time": out_time[valid],
        "exit_price": out_price[valid],
        "outcome_code": out_code[valid],
        "timeframe": timeframe,
    })
    return frame


def add_pnl(frame: pd.DataFrame) -> pd.DataFrame:
    if frame.empty:
        for name in ("gross_pnl_tl", "fees_tl", "funding_tl", "net_pnl_tl",
                     "net_return_pct", "outcome", "hold_hours"):
            frame[name] = pd.Series(dtype="float64")
        return frame
    entry = frame["entry_price"].to_numpy(dtype="float64")
    exit_ = frame["exit_price"].to_numpy(dtype="float64")
    ratio = exit_ / entry
    gross = STAKE_TL * (ratio - 1.0)
    fee_rate = frame["timeframe"].map(FEE_RATE).to_numpy(dtype="float64")
    fees = fee_rate * (STAKE_TL + STAKE_TL * ratio)
    hold_hours = (frame["exit_time"].to_numpy() - frame["entry_time"].to_numpy()) / 3_600_000.0
    funding = np.where(
        frame["timeframe"].to_numpy() == "4h",
        FUNDING_PER_8H * STAKE_TL * hold_hours / 8.0,
        0.0,
    )
    net = gross - fees - funding
    frame = frame.copy()
    frame["gross_pnl_tl"] = gross
    frame["fees_tl"] = fees
    frame["funding_tl"] = funding
    frame["net_pnl_tl"] = net
    frame["net_return_pct"] = net / STAKE_TL * 100.0
    frame["hold_hours"] = hold_hours
    frame["outcome"] = np.select(
        [frame["outcome_code"].to_numpy() == OUTCOME_TP,
         frame["outcome_code"].to_numpy() == OUTCOME_SL],
        ["TP", "SL"],
        default="TIME",
    )
    return frame


def admit_portfolio(candidates: pd.DataFrame) -> pd.DataFrame:
    """Chronological 10-slot, 10k-ticket admission for one scenario."""
    if candidates.empty:
        return candidates
    ordered = candidates.sort_values(
        ["signal_time", "score", "range_value", "symbol"],
        ascending=[True, False, False, True],
        na_position="last",
        kind="mergesort",
    )
    accepted: list[int] = []
    active: list[int] = []
    signal = ordered["signal_time"].to_numpy(dtype="int64")
    exit_ms = ordered["exit_time"].to_numpy(dtype="int64")
    for pos in range(len(ordered)):
        now = int(signal[pos])
        while active and active[0] <= now:
            heapq.heappop(active)
        if len(active) >= MAX_OPEN:
            continue
        accepted.append(pos)
        heapq.heappush(active, int(exit_ms[pos]))
    return ordered.iloc[accepted].copy()


def apply_singular(normal: pd.DataFrame) -> pd.DataFrame:
    """Block a normal trade while the same scenario+symbol is already open."""
    if normal.empty:
        return normal
    accepted: list[int] = []
    for _, group in normal.groupby(["scenario_id", "symbol"], sort=False,
                                   observed=True):
        group = group.sort_values(
            ["signal_time", "entry_time", "exit_time"], kind="mergesort"
        )
        last_exit = None
        for idx, row in group.iterrows():
            if last_exit is not None and row["signal_time"] < last_exit:
                continue
            accepted.append(idx)
            last_exit = row["exit_time"]
    return normal.loc[sorted(accepted)].copy()


def portfolio_admit_all(candidates: pd.DataFrame) -> pd.DataFrame:
    accepted: list[pd.DataFrame] = []
    for sid, group in candidates.groupby("scenario_id", sort=False):
        admitted = admit_portfolio(group)
        if not admitted.empty:
            accepted.append(admitted)
    if not accepted:
        return candidates.iloc[0:0].copy()
    return pd.concat(accepted, ignore_index=True)
