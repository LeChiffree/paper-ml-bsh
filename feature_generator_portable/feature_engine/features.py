from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd

from .config import ROOT


FEATURE_ORDER: list[str] = json.loads((ROOT / "feature_order.json").read_text(encoding="utf-8"))
PERIODS = (5, 15, 50, 200)
MACRO_COLUMNS = [
    *(f"BTC_Ret_{n}m" for n in PERIODS), "BTC_Z_Score_15", "BTC_Z_Score_50",
    "BTC_Volume_vs_SMA50_Pct", *(f"ETH_BTC_Ret_{n}m" for n in PERIODS),
]


def safe_div(a: pd.Series, b: pd.Series) -> pd.Series:
    return a / b.replace(0, np.nan)


def divpct(a: pd.Series, b: pd.Series) -> pd.Series:
    return (safe_div(a, b) - 1.0) * 100.0


def rsi(close: pd.Series, n: int) -> pd.Series:
    delta = close.diff()
    up, down = delta.clip(lower=0), -delta.clip(upper=0)
    avg_up = up.ewm(alpha=1 / n, adjust=False, min_periods=n).mean()
    avg_down = down.ewm(alpha=1 / n, adjust=False, min_periods=n).mean()
    rs = safe_div(avg_up, avg_down)
    result = 100 - 100 / (1 + rs)
    return result.where(avg_down.ne(0), 100).where(avg_up.ne(0), 0)


def true_range(high: pd.Series, low: pd.Series, close: pd.Series) -> pd.Series:
    previous = close.shift(1)
    return pd.concat([high - low, (high - previous).abs(), (low - previous).abs()], axis=1).max(axis=1)


def psar(high: pd.Series, low: pd.Series, step: float = 0.02, maximum: float = 0.2) -> pd.Series:
    h, l = high.to_numpy(), low.to_numpy()
    output = np.full(len(h), np.nan)
    if len(h) < 2:
        return pd.Series(output, index=high.index)
    bull, acceleration, extreme = True, step, h[0]
    output[0] = l[0]
    for i in range(1, len(h)):
        sar = output[i - 1] + acceleration * (extreme - output[i - 1])
        if bull:
            sar = min(sar, l[i - 1], l[i - 2] if i > 1 else l[i - 1])
            if l[i] < sar:
                bull, sar, extreme, acceleration = False, extreme, l[i], step
            elif h[i] > extreme:
                extreme, acceleration = h[i], min(maximum, acceleration + step)
        else:
            sar = max(sar, h[i - 1], h[i - 2] if i > 1 else h[i - 1])
            if h[i] > sar:
                bull, sar, extreme, acceleration = True, extreme, h[i], step
            elif l[i] < extreme:
                extreme, acceleration = l[i], min(maximum, acceleration + step)
        output[i] = sar
    return pd.Series(output, index=high.index)


def build_macro(frames: dict[str, pd.DataFrame]) -> pd.DataFrame:
    btc, eth = frames["BTCUSDT"], frames["ETHUSDT"]
    macro = btc[["open_time"]].copy()
    btc_close = btc["close"].astype(float).reset_index(drop=True)
    btc_volume = btc["volume"].astype(float).reset_index(drop=True)
    eth_series = eth.set_index("open_time")["close"].reindex(macro["open_time"]).reset_index(drop=True)
    for n in PERIODS:
        macro[f"BTC_Ret_{n}m"] = btc_close.pct_change(n, fill_method=None) * 100
    for n in (15, 50):
        macro[f"BTC_Z_Score_{n}"] = (btc_close - btc_close.rolling(n).mean()) / btc_close.rolling(n).std(ddof=0).replace(0, np.nan)
    macro["BTC_Volume_vs_SMA50_Pct"] = divpct(btc_volume, btc_volume.rolling(50).mean())
    ratio = safe_div(eth_series, btc_close)
    for n in PERIODS:
        macro[f"ETH_BTC_Ret_{n}m"] = ratio.pct_change(n, fill_method=None) * 100
    return macro


def compute_features(
    raw: pd.DataFrame,
    macro: pd.DataFrame,
    market_share: pd.Series | None = None,
) -> pd.DataFrame:
    df = raw.copy().reset_index(drop=True)
    o, h, l, c, v = (df[name].astype(float) for name in ("open", "high", "low", "close", "volume"))
    qv = df["quote_volume"].astype(float)
    trades = df["trade_count"].astype(float)
    taker_quote = df["taker_buy_quote_volume"].astype(float)
    tr = true_range(h, l, c)
    emas = {n: c.ewm(span=n, adjust=False, min_periods=n).mean() for n in PERIODS}

    for n in PERIODS:
        df[f"Return_{n}m"] = c.pct_change(n, fill_method=None) * 100
        df[f"Max_Drawdown_{n}"] = (safe_div(c, h.rolling(n).max()) - 1) * 100
        df[f"Min_Runup_{n}"] = (safe_div(c, l.rolling(n).min()) - 1) * 100
        df[f"Close_vs_EMA_{n}_Pct"] = divpct(c, emas[n])
        df[f"RSI_{n}"] = rsi(c, n)
        mean, std = c.rolling(n).mean(), c.rolling(n).std(ddof=0)
        df[f"Z_Score_{n}"] = safe_div(c - mean, std)
        atr = tr.ewm(alpha=1 / n, adjust=False, min_periods=n).mean()
        df[f"ATR_vs_Close_{n}_Pct"] = safe_div(atr, c) * 100
        df[f"Volume_vs_SMA_{n}_Pct"] = divpct(v, v.rolling(n).mean())

    df["EMA5_vs_EMA15_Pct"] = divpct(emas[5], emas[15])
    df["EMA15_vs_EMA50_Pct"] = divpct(emas[15], emas[50])
    df["EMA50_vs_EMA200_Pct"] = divpct(emas[50], emas[200])
    df["Close_vs_PSAR_Pct"] = divpct(c, psar(h, l))
    conversion = (h.rolling(9).max() + l.rolling(9).min()) / 2
    base = (h.rolling(26).max() + l.rolling(26).min()) / 2
    span_a = ((conversion + base) / 2).shift(26)
    span_b = ((h.rolling(52).max() + l.rolling(52).min()) / 2).shift(26)
    cloud_top = pd.concat([span_a, span_b], axis=1).max(axis=1)
    cloud_bottom = pd.concat([span_a, span_b], axis=1).min(axis=1)
    df["Close_vs_Ichimoku_Kumo_Pct"] = np.where(
        c > cloud_top, (safe_div(c, cloud_top) - 1) * 100,
        np.where(c < cloud_bottom, (safe_div(c, cloud_bottom) - 1) * 100, 0),
    )

    rsi14 = rsi(c, 14)
    df["RSI_14_Slope_5"] = (rsi14 - rsi14.shift(5)) / 5
    df["RSI_14_Slope_15"] = (rsi14 - rsi14.shift(15)) / 15
    ema12 = c.ewm(span=12, adjust=False, min_periods=12).mean()
    ema26 = c.ewm(span=26, adjust=False, min_periods=26).mean()
    macd = ema12 - ema26
    macd_signal = macd.ewm(span=9, adjust=False, min_periods=9).mean()
    macd_hist = macd - macd_signal
    df["MACD_Line_vs_Signal_Pct"] = (macd - macd_signal) / (macd_signal.abs() + 1e-12) * 100
    df["MACD_Hist_Slope_5"] = (macd_hist - macd_hist.shift(5)) / 5
    low14, high14 = l.rolling(14).min(), h.rolling(14).max()
    stoch_k = safe_div(c - low14, high14 - low14) * 100
    stoch_d = stoch_k.rolling(3).mean()
    df["Stoch_K_vs_D_Dist"] = stoch_k - stoch_d

    mid20, std20 = c.rolling(20).mean(), c.rolling(20).std(ddof=0)
    bb_upper, bb_lower = mid20 + 2 * std20, mid20 - 2 * std20
    bb_width = safe_div(bb_upper - bb_lower, mid20) * 100
    df["BB_Width_vs_SMA50_Pct"] = divpct(bb_width, bb_width.rolling(50).mean())
    df["Close_vs_BB_Upper_Pct"] = divpct(c, bb_upper)
    df["Close_vs_BB_Lower_Pct"] = divpct(c, bb_lower)
    atr20 = tr.ewm(alpha=1 / 20, adjust=False, min_periods=20).mean()
    keltner_mid = c.ewm(span=20, adjust=False, min_periods=20).mean()
    df["Squeeze_On"] = ((bb_upper < keltner_mid + 1.5 * atr20) & (bb_lower > keltner_mid - 1.5 * atr20)).astype("int8")
    df["Choppiness_Index_14"] = 100 * np.log10(
        safe_div(tr.rolling(14).sum(), h.rolling(14).max() - l.rolling(14).min())
    ) / np.log10(14)

    direction = np.sign(c.diff()).fillna(0)
    obv = (direction * v).cumsum()
    for n in PERIODS:
        df[f"OBV_Slope_{n}"] = (obv - obv.shift(n)) / n
    candle_range = h - l
    clv = safe_div(2 * c - h - l, candle_range).fillna(0)
    df["Volume_Pressure"] = v * clv
    df["CMF_20"] = safe_div((clv * v).rolling(20).sum(), v.rolling(20).sum())
    typical = (h + l + c) / 3
    flow = typical * v
    positive = flow.where(typical.diff() > 0, 0).rolling(14).sum()
    negative = flow.where(typical.diff() < 0, 0).rolling(14).sum()
    mfi = 100 - 100 / (1 + safe_div(positive, negative))
    df["MFI_14_vs_SMA50_Pct"] = divpct(mfi, mfi.rolling(50).mean())
    df["Close_Location_Value_CLV"] = clv
    df["Upper_Wick_Pct"] = safe_div(h - np.maximum(o, c), candle_range) * 100
    df["Lower_Wick_Pct"] = safe_div(np.minimum(o, c) - l, candle_range) * 100
    df["Candle_Body_Pct"] = safe_div((c - o).abs(), candle_range) * 100
    timestamp = pd.to_datetime(df["open_time"], unit="ms", utc=True)
    hour = timestamp.dt.hour + timestamp.dt.minute / 60
    df["Hour_Sin"] = np.sin(2 * np.pi * hour / 24)
    df["Hour_Cos"] = np.cos(2 * np.pi * hour / 24)
    df["Day_of_Week"] = timestamp.dt.dayofweek.astype("int8")

    macro_indexed = macro.set_index("open_time")
    for column in MACRO_COLUMNS:
        df[column] = df["open_time"].map(macro_indexed[column])

    # Sonraki 50 interaction kolonunu tek tek eklerken pandas blok parcalanmasini onler.
    df = df.copy()
    one_bar_return = c.pct_change(fill_method=None)
    speed5, speed15, speed50, speed200 = (df[f"Return_{n}m"] / n for n in PERIODS)
    df["MomentumSpeed_Diff_5_15"] = speed5 - speed15
    df["MomentumSpeed_Diff_15_50"] = speed15 - speed50
    df["MomentumSpeed_Diff_50_200"] = speed50 - speed200
    df["Momentum_Curvature_5_15_50"] = speed5 - 2 * speed15 + speed50
    df["Momentum_Curvature_15_50_200"] = speed15 - 2 * speed50 + speed200
    for n in (15, 50):
        path = one_bar_return.abs().rolling(n).sum()
        df[f"Price_Efficiency_{n}"] = safe_div((c / c.shift(n) - 1).abs(), path)
    ema_signs = pd.concat([
        np.sign(df["EMA5_vs_EMA15_Pct"]), np.sign(df["EMA15_vs_EMA50_Pct"]),
        np.sign(df["EMA50_vs_EMA200_Pct"]),
    ], axis=1)
    df["Trend_Alignment_Score"] = ema_signs.mean(axis=1)
    trend_spread = (
        df["EMA5_vs_EMA15_Pct"].abs() + df["EMA15_vs_EMA50_Pct"].abs()
        + df["EMA50_vs_EMA200_Pct"].abs()
    ) / 3
    df["Trend_Strength_ATR15"] = safe_div(trend_spread, df["ATR_vs_Close_15_Pct"])
    high50, low50 = h.rolling(50).max(), l.rolling(50).min()
    df["Breakout_Position_50"] = 2 * safe_div(c - low50, high50 - low50) - 1
    df["ATR_Ratio_5_50"] = safe_div(df["ATR_vs_Close_5_Pct"], df["ATR_vs_Close_50_Pct"]) - 1
    df["ATR_Ratio_15_200"] = safe_div(df["ATR_vs_Close_15_Pct"], df["ATR_vs_Close_200_Pct"]) - 1
    df["ATR_Acceleration_5_15"] = safe_div(df["ATR_vs_Close_5_Pct"], df["ATR_vs_Close_15_Pct"]) - 1
    df["ZScore_Spread_5_50"] = df["Z_Score_5"] - df["Z_Score_50"]
    df["ZScore_Spread_15_200"] = df["Z_Score_15"] - df["Z_Score_200"]
    bb_width_pct = 4 * std20 / mid20.replace(0, np.nan) * 100
    df["BBWidth_to_ATR15"] = safe_div(bb_width_pct, df["ATR_vs_Close_15_Pct"])
    for n in (5, 15, 50):
        df[f"VolAdj_Return_{n}"] = safe_div(df[f"Return_{n}m"], df[f"ATR_vs_Close_{n}_Pct"] * np.sqrt(n))
    downside = one_bar_return.clip(upper=0).rolling(50).std(ddof=0)
    upside = one_bar_return.clip(lower=0).rolling(50).std(ddof=0)
    df["Downside_Upside_Vol_Ratio_50"] = safe_div(downside, upside)
    vr5 = 1 + df["Volume_vs_SMA_5_Pct"] / 100
    vr15 = 1 + df["Volume_vs_SMA_15_Pct"] / 100
    vr50 = 1 + df["Volume_vs_SMA_50_Pct"] / 100
    vr200 = 1 + df["Volume_vs_SMA_200_Pct"] / 100
    df["Volume_Ratio_5_50"] = safe_div(vr5, vr50) - 1
    df["Volume_Ratio_15_200"] = safe_div(vr15, vr200) - 1
    df["Price_Volume_Confirm_5"] = np.sign(df["Return_5m"]) * (vr5 - 1)
    df["Price_Volume_Confirm_15"] = np.sign(df["Return_15m"]) * (vr15 - 1)
    df["CMF_Return_Interaction_15"] = df["CMF_20"] * df["Return_15m"]
    df["CLV_Volume_Interaction"] = df["Close_Location_Value_CLV"] * (vr50 - 1)
    df["OBV_Normalized_15"] = safe_div(df["OBV_Slope_15"], v.rolling(15).mean())
    df["OBV_Normalized_50"] = safe_div(df["OBV_Slope_50"], v.rolling(50).mean())
    imbalance = 2 * safe_div(taker_quote, qv) - 1
    df["Taker_Buy_Imbalance"] = imbalance
    df["Taker_Imbalance_Change_15"] = imbalance - imbalance.shift(15)
    average_trade = safe_div(qv, trades)
    df["Avg_Trade_Size_vs_SMA50_Pct"] = (safe_div(average_trade, average_trade.rolling(50).mean()) - 1) * 100
    df["Avg_Trade_Size_Trend_15_Pct"] = (safe_div(average_trade, average_trade.shift(15)) - 1) * 100
    df["Signed_Body_Pct"] = np.sign(c - o) * df["Candle_Body_Pct"]
    df["Wick_Imbalance_Pct"] = df["Lower_Wick_Pct"] - df["Upper_Wick_Pct"]
    df["Rejection_Strength"] = df["Close_Location_Value_CLV"] * (df["Upper_Wick_Pct"] + df["Lower_Wick_Pct"]) / 100
    atr15_price = df["ATR_vs_Close_15_Pct"] / 100 * c
    df["Range_vs_ATR15"] = safe_div(candle_range, atr15_price)
    df["Open_Gap_Pct"] = (safe_div(o, c.shift(1)) - 1) * 100
    df["Inside_Bar"] = ((h <= h.shift(1)) & (l >= l.shift(1))).astype("int8")
    df["Outside_Bar"] = ((h >= h.shift(1)) & (l <= l.shift(1))).astype("int8")
    df["Range_Compression_20"] = safe_div(candle_range, candle_range.rolling(20).mean()) - 1
    df["RSI_Spread_5_50"] = df["RSI_5"] - df["RSI_50"]
    df["RSI_Spread_15_200"] = df["RSI_15"] - df["RSI_200"]
    df["RSI_Slope_Acceleration"] = df["RSI_14_Slope_5"] - df["RSI_14_Slope_15"]
    df["RSI_EMA_Trend_Interaction"] = ((df["RSI_15"] - 50) / 50) * (df["EMA15_vs_EMA50_Pct"] / 100)
    df["MACD_Stoch_Agreement"] = np.tanh(df["MACD_Line_vs_Signal_Pct"] / 100) * np.tanh(df["Stoch_K_vs_D_Dist"] / 25)
    df["MeanReversion_Trend_Tension"] = df["Z_Score_15"] * np.tanh(df["EMA15_vs_EMA50_Pct"] / 2)
    df["Alt_vs_BTC_Ret_5"] = df["Return_5m"] - df["BTC_Ret_5m"]
    df["Alt_vs_BTC_Ret_15"] = df["Return_15m"] - df["BTC_Ret_15m"]
    df["BTC_Corr_50"] = df["Return_5m"].rolling(50).corr(df["BTC_Ret_5m"])
    df["BTC_Beta_200"] = safe_div(
        df["Return_5m"].rolling(200).cov(df["BTC_Ret_5m"]),
        df["BTC_Ret_5m"].rolling(200).var(ddof=1),
    )
    df["Quote_Volume_Market_Share"] = market_share if market_share is not None else np.nan
    df[FEATURE_ORDER] = df[FEATURE_ORDER].replace([np.inf, -np.inf], np.nan)
    return df


def compute_latest_matrix(
    frames: dict[str, pd.DataFrame],
    server_time_ms: int,
    retain_enriched: bool = True,
) -> tuple[pd.DataFrame, dict[str, pd.DataFrame]]:
    # Acik mum feature veya pozisyon bariyer kontrolunde kullanilmaz.
    closed = {
        symbol: frame.loc[frame["close_time"] < server_time_ms].copy().reset_index(drop=True)
        for symbol, frame in frames.items()
    }
    closed = {symbol: frame for symbol, frame in closed.items() if len(frame) >= 200}
    if "BTCUSDT" not in closed or "ETHUSDT" not in closed:
        raise ValueError("BTCUSDT ve ETHUSDT icin en az 200 kapanmis mum gerekli")
    macro = build_macro(closed)
    quote_table = pd.concat(
        [frame.set_index("open_time")["quote_volume"].rename(symbol) for symbol, frame in closed.items()], axis=1
    )
    market_total = quote_table.sum(axis=1, min_count=1)
    target_open_time = int(closed["BTCUSDT"]["open_time"].iloc[-1])
    rows: list[pd.Series] = []
    enriched: dict[str, pd.DataFrame] = {}
    for symbol, frame in closed.items():
        share = safe_div(frame.set_index("open_time")["quote_volume"], market_total).reindex(frame["open_time"]).reset_index(drop=True)
        calculated = compute_features(frame, macro, share)
        if retain_enriched:
            enriched[symbol] = calculated
        target_rows = calculated.loc[calculated["open_time"] == target_open_time]
        if target_rows.empty:
            continue
        latest = target_rows.iloc[-1].copy()
        latest["symbol"] = symbol
        rows.append(latest)
    matrix = pd.DataFrame(rows).set_index("symbol", drop=False)
    return matrix, enriched


def volume_change_and_bin(frame: pd.DataFrame) -> tuple[float, float, str, str]:
    volume = frame["volume"].astype(float)
    prev5 = 100 * (volume.iloc[-1] / volume.iloc[-6:-1].mean() - 1) if len(volume) >= 6 else np.nan
    prev1 = 100 * (volume.iloc[-1] / volume.iloc[-2] - 1) if len(volume) >= 2 and volume.iloc[-2] else np.nan
    return float(prev5), float(prev1), volume_bin(prev5), volume_bin(prev1)


def volume_bin(value: float) -> str:
    if not np.isfinite(value):
        return "unknown"
    edges = [-50, -20, -10, 0, 10, 20, 50, 100, 200, 500]
    labels = ["<-50", "-50/-20", "-20/-10", "-10/0", "0/10", "10/20", "20/50", "50/100", "100/200", "200/500", ">500"]
    return labels[int(np.searchsorted(edges, value, side="right"))]
