from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Iterable

import numpy as np
import pandas as pd


EPS = 1e-12
BASE_COLUMNS = [
    "open",
    "high",
    "low",
    "close",
    "volume",
    "quote_volume",
    "trade_count",
    "taker_buy_base_volume",
    "taker_buy_quote_volume",
]

# These features are intentionally marked in the UI. The package does not contain
# the original training implementation, so the formulas below are best-effort.
PIPELINE_DEFINED = {
    "RSI_14_Slope_5",
    "RSI_14_Slope_15",
    "MACD_Hist_Slope_5",
    "Volume_Pressure",
    "MomentumSpeed_Diff_5_15",
    "MomentumSpeed_Diff_15_50",
    "MomentumSpeed_Diff_50_200",
    "Momentum_Curvature_5_15_50",
    "Momentum_Curvature_15_50_200",
    "Trend_Alignment_Score",
    "Price_Volume_Confirm_5",
    "Price_Volume_Confirm_15",
    "CMF_Return_Interaction_15",
    "CLV_Volume_Interaction",
    "Taker_Imbalance_Change_15",
    "Rejection_Strength",
    "RSI_Slope_Acceleration",
    "RSI_EMA_Trend_Interaction",
    "MACD_Stoch_Agreement",
    "MeanReversion_Trend_Tension",
}

UNDER_SPECIFIED = {
    "BB_Width_vs_SMA50_Pct",
    "Close_vs_BB_Upper_Pct",
    "Close_vs_BB_Lower_Pct",
    "Squeeze_On",
    "OBV_Slope_5",
    "OBV_Slope_15",
    "OBV_Slope_50",
    "OBV_Slope_200",
    "BTC_Volume_vs_SMA50_Pct",
    "Trend_Strength_ATR15",
    "ATR_Acceleration_5_15",
    "BBWidth_to_ATR15",
    "VolAdj_Return_5",
    "VolAdj_Return_15",
    "VolAdj_Return_50",
    "Downside_Upside_Vol_Ratio_50",
    "OBV_Normalized_15",
    "OBV_Normalized_50",
    "Taker_Buy_Imbalance",
    "Avg_Trade_Size_vs_SMA50_Pct",
    "Avg_Trade_Size_Trend_15_Pct",
    "Wick_Imbalance_Pct",
    "Range_Compression_20",
    "Quote_Volume_Market_Share",
}


def _safe_div(a: pd.Series, b: pd.Series) -> pd.Series:
    return a / b.where(b.abs() > EPS)


def _pct_ratio(a: pd.Series, b: pd.Series) -> pd.Series:
    return 100.0 * (_safe_div(a, b) - 1.0)


def _ema(series: pd.Series, span: int) -> pd.Series:
    return series.ewm(span=span, adjust=False, min_periods=span).mean()


def _rsi(close: pd.Series, period: int) -> pd.Series:
    delta = close.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.ewm(alpha=1 / period, adjust=False, min_periods=period).mean()
    avg_loss = loss.ewm(alpha=1 / period, adjust=False, min_periods=period).mean()
    rs = _safe_div(avg_gain, avg_loss)
    out = 100 - (100 / (1 + rs))
    return out.where(avg_loss > EPS, 100.0).where(avg_gain > EPS, 0.0)


def _true_range(df: pd.DataFrame) -> pd.Series:
    previous = df["close"].shift(1)
    return pd.concat(
        [
            df["high"] - df["low"],
            (df["high"] - previous).abs(),
            (df["low"] - previous).abs(),
        ],
        axis=1,
    ).max(axis=1)


def _atr(df: pd.DataFrame, period: int) -> pd.Series:
    return _true_range(df).ewm(
        alpha=1 / period, adjust=False, min_periods=period
    ).mean()


def _zscore(series: pd.Series, period: int) -> pd.Series:
    mean = series.rolling(period, min_periods=period).mean()
    std = series.rolling(period, min_periods=period).std(ddof=0)
    return _safe_div(series - mean, std)


def _rolling_slope(series: pd.Series, period: int) -> pd.Series:
    x = np.arange(period, dtype=float)
    x = x - x.mean()
    denominator = float(np.dot(x, x))

    def slope(values: np.ndarray) -> float:
        if np.isnan(values).any():
            return np.nan
        return float(np.dot(x, values - values.mean()) / denominator)

    return series.rolling(period, min_periods=period).apply(slope, raw=True)


def _psar(df: pd.DataFrame, step: float = 0.02, maximum: float = 0.2) -> pd.Series:
    high = df["high"].to_numpy(float)
    low = df["low"].to_numpy(float)
    close = df["close"].to_numpy(float)
    out = np.full(len(df), np.nan)
    if len(df) < 2:
        return pd.Series(out, index=df.index)
    rising = close[1] >= close[0]
    extreme = high[0] if rising else low[0]
    sar = low[0] if rising else high[0]
    acceleration = step
    out[0] = sar
    for i in range(1, len(df)):
        sar = sar + acceleration * (extreme - sar)
        if rising:
            sar = min(sar, low[i - 1], low[i - 2] if i > 1 else low[i - 1])
            if low[i] < sar:
                rising = False
                sar = extreme
                extreme = low[i]
                acceleration = step
            elif high[i] > extreme:
                extreme = high[i]
                acceleration = min(maximum, acceleration + step)
        else:
            sar = max(sar, high[i - 1], high[i - 2] if i > 1 else high[i - 1])
            if high[i] > sar:
                rising = True
                sar = extreme
                extreme = high[i]
                acceleration = step
            elif low[i] < extreme:
                extreme = low[i]
                acceleration = min(maximum, acceleration + step)
        out[i] = sar
    return pd.Series(out, index=df.index)


def _mfi(df: pd.DataFrame, period: int = 14) -> pd.Series:
    typical = (df["high"] + df["low"] + df["close"]) / 3
    flow = typical * df["volume"]
    positive = flow.where(typical.diff() > 0, 0.0)
    negative = flow.where(typical.diff() < 0, 0.0)
    pos_sum = positive.rolling(period, min_periods=period).sum()
    neg_sum = negative.rolling(period, min_periods=period).sum()
    ratio = _safe_div(pos_sum, neg_sum)
    return (100 - 100 / (1 + ratio)).where(neg_sum > EPS, 100.0)


def _down_up_vol_ratio(returns: pd.Series, period: int = 50) -> pd.Series:
    def calculate(values: np.ndarray) -> float:
        down = values[values < 0]
        up = values[values > 0]
        if len(down) < 2 or len(up) < 2:
            return np.nan
        up_std = np.std(up, ddof=0)
        return np.nan if up_std <= EPS else float(np.std(down, ddof=0) / up_std)

    return returns.rolling(period, min_periods=period).apply(calculate, raw=True)


def _ensure_frame(frame: pd.DataFrame) -> pd.DataFrame:
    missing = [column for column in BASE_COLUMNS if column not in frame.columns]
    if missing:
        raise ValueError(f"Missing candle columns: {missing}")
    out = frame.copy().sort_index()
    for column in BASE_COLUMNS:
        out[column] = pd.to_numeric(out[column], errors="coerce")
    return out


@dataclass
class FeatureResult:
    values: pd.Series
    missing_count: int
    approximate_count: int


def compute_features(
    candles: pd.DataFrame,
    btc_candles: pd.DataFrame,
    eth_candles: pd.DataFrame,
    market_quote_total: pd.Series,
    feature_order: Iterable[str],
) -> FeatureResult:
    df = _ensure_frame(candles)
    btc = _ensure_frame(btc_candles).reindex(df.index)
    eth = _ensure_frame(eth_candles).reindex(df.index)
    f = pd.DataFrame(index=df.index)
    for column in BASE_COLUMNS:
        f[column] = df[column]

    ema: dict[int, pd.Series] = {}
    rsi: dict[int, pd.Series] = {}
    atr: dict[int, pd.Series] = {}
    zscore: dict[int, pd.Series] = {}
    returns: dict[int, pd.Series] = {}
    for period in (5, 15, 50, 200):
        returns[period] = df["close"].pct_change(period) * 100
        ema[period] = _ema(df["close"], period)
        rsi[period] = _rsi(df["close"], period)
        atr[period] = _atr(df, period)
        zscore[period] = _zscore(df["close"], period)
        suffix = f"{period}m" if period in (5, 15, 50, 200) else str(period)
        f[f"Return_{suffix}"] = returns[period]
        f[f"Max_Drawdown_{period}"] = _pct_ratio(
            df["close"], df["high"].rolling(period, min_periods=period).max()
        )
        f[f"Min_Runup_{period}"] = _pct_ratio(
            df["close"], df["low"].rolling(period, min_periods=period).min()
        )
        f[f"Close_vs_EMA_{period}_Pct"] = _pct_ratio(df["close"], ema[period])
        f[f"RSI_{period}"] = rsi[period]
        f[f"Z_Score_{period}"] = zscore[period]
        f[f"ATR_vs_Close_{period}_Pct"] = 100 * _safe_div(atr[period], df["close"])
        f[f"Volume_vs_SMA_{period}_Pct"] = _pct_ratio(
            df["volume"], df["volume"].rolling(period, min_periods=period).mean()
        )

    f["EMA5_vs_EMA15_Pct"] = _pct_ratio(ema[5], ema[15])
    f["EMA15_vs_EMA50_Pct"] = _pct_ratio(ema[15], ema[50])
    f["EMA50_vs_EMA200_Pct"] = _pct_ratio(ema[50], ema[200])
    psar = _psar(df)
    f["Close_vs_PSAR_Pct"] = _pct_ratio(df["close"], psar)
    conversion = (df["high"].rolling(9).max() + df["low"].rolling(9).min()) / 2
    base = (df["high"].rolling(26).max() + df["low"].rolling(26).min()) / 2
    span_a = ((conversion + base) / 2).shift(26)
    span_b = ((df["high"].rolling(52).max() + df["low"].rolling(52).min()) / 2).shift(26)
    cloud_mid = (span_a + span_b) / 2
    f["Close_vs_Ichimoku_Kumo_Pct"] = _pct_ratio(df["close"], cloud_mid)

    rsi14 = _rsi(df["close"], 14)
    rsi_slope5 = _rolling_slope(rsi14, 5)
    rsi_slope15 = _rolling_slope(rsi14, 15)
    f["RSI_14_Slope_5"] = rsi_slope5
    f["RSI_14_Slope_15"] = rsi_slope15
    macd = _ema(df["close"], 12) - _ema(df["close"], 26)
    macd_signal = _ema(macd, 9)
    macd_hist = macd - macd_signal
    f["MACD_Line_vs_Signal_Pct"] = 100 * _safe_div(macd - macd_signal, macd_signal.abs())
    f["MACD_Hist_Slope_5"] = _rolling_slope(macd_hist, 5)
    low14 = df["low"].rolling(14).min()
    high14 = df["high"].rolling(14).max()
    stoch_k = 100 * _safe_div(df["close"] - low14, high14 - low14)
    stoch_d = stoch_k.rolling(3).mean()
    f["Stoch_K_vs_D_Dist"] = stoch_k - stoch_d

    sma20 = df["close"].rolling(20).mean()
    std20 = df["close"].rolling(20).std(ddof=0)
    bb_upper = sma20 + 2 * std20
    bb_lower = sma20 - 2 * std20
    bb_width = bb_upper - bb_lower
    f["BB_Width_vs_SMA50_Pct"] = 100 * _safe_div(bb_width, df["close"].rolling(50).mean())
    f["Close_vs_BB_Upper_Pct"] = _pct_ratio(df["close"], bb_upper)
    f["Close_vs_BB_Lower_Pct"] = _pct_ratio(df["close"], bb_lower)
    kc_mid = _ema(df["close"], 20)
    atr20 = _atr(df, 20)
    f["Squeeze_On"] = ((bb_upper < kc_mid + 2 * atr20) & (bb_lower > kc_mid - 2 * atr20)).astype(float)
    tr14_sum = _true_range(df).rolling(14).sum()
    range14 = df["high"].rolling(14).max() - df["low"].rolling(14).min()
    f["Choppiness_Index_14"] = 100 * np.log10(_safe_div(tr14_sum, range14)) / math.log10(14)

    direction = np.sign(df["close"].diff()).fillna(0)
    obv = (direction * df["volume"]).cumsum()
    for period in (5, 15, 50, 200):
        f[f"OBV_Slope_{period}"] = _rolling_slope(obv, period)
    one_bar_return = df["close"].pct_change()
    f["Volume_Pressure"] = _safe_div(
        (np.sign(one_bar_return) * df["volume"]).rolling(20).sum(),
        df["volume"].rolling(20).sum(),
    )
    money_multiplier = _safe_div(
        (df["close"] - df["low"]) - (df["high"] - df["close"]),
        df["high"] - df["low"],
    ).fillna(0)
    cmf20 = _safe_div(
        (money_multiplier * df["volume"]).rolling(20).sum(),
        df["volume"].rolling(20).sum(),
    )
    f["CMF_20"] = cmf20
    mfi14 = _mfi(df, 14)
    f["MFI_14_vs_SMA50_Pct"] = _pct_ratio(mfi14, mfi14.rolling(50).mean())

    candle_range = df["high"] - df["low"]
    upper_wick = df["high"] - pd.concat([df["open"], df["close"]], axis=1).max(axis=1)
    lower_wick = pd.concat([df["open"], df["close"]], axis=1).min(axis=1) - df["low"]
    f["Close_Location_Value_CLV"] = _safe_div(
        (df["close"] - df["low"]) - (df["high"] - df["close"]), candle_range
    )
    f["Upper_Wick_Pct"] = 100 * _safe_div(upper_wick, candle_range)
    f["Lower_Wick_Pct"] = 100 * _safe_div(lower_wick, candle_range)
    f["Candle_Body_Pct"] = 100 * _safe_div((df["close"] - df["open"]).abs(), candle_range)
    hours = pd.Series(df.index.hour, index=df.index, dtype=float)
    f["Hour_Sin"] = np.sin(2 * np.pi * hours / 24)
    f["Hour_Cos"] = np.cos(2 * np.pi * hours / 24)
    f["Day_of_Week"] = pd.Series(df.index.dayofweek, index=df.index, dtype=float)

    btc_close = btc["close"]
    btc_returns: dict[int, pd.Series] = {}
    ratio = _safe_div(eth["close"], btc_close)
    for period in (5, 15, 50, 200):
        btc_returns[period] = btc_close.pct_change(period) * 100
        f[f"BTC_Ret_{period}m"] = btc_returns[period]
        f[f"ETH_BTC_Ret_{period}m"] = ratio.pct_change(period) * 100
    f["BTC_Z_Score_15"] = _zscore(btc_close, 15)
    f["BTC_Z_Score_50"] = _zscore(btc_close, 50)
    f["BTC_Volume_vs_SMA50_Pct"] = _pct_ratio(
        btc["quote_volume"], btc["quote_volume"].rolling(50).mean()
    )

    speed = {period: returns[period] / period for period in (5, 15, 50, 200)}
    f["MomentumSpeed_Diff_5_15"] = speed[5] - speed[15]
    f["MomentumSpeed_Diff_15_50"] = speed[15] - speed[50]
    f["MomentumSpeed_Diff_50_200"] = speed[50] - speed[200]
    f["Momentum_Curvature_5_15_50"] = (speed[5] - speed[15]) - (speed[15] - speed[50])
    f["Momentum_Curvature_15_50_200"] = (speed[15] - speed[50]) - (speed[50] - speed[200])
    abs_move = df["close"].diff().abs()
    for period in (15, 50):
        f[f"Price_Efficiency_{period}"] = _safe_div(
            (df["close"] - df["close"].shift(period)).abs(),
            abs_move.rolling(period).sum(),
        )
    trend_votes = pd.concat(
        [np.sign(df["close"] - ema[p]) for p in (5, 15, 50, 200)], axis=1
    )
    f["Trend_Alignment_Score"] = trend_votes.mean(axis=1)
    f["Trend_Strength_ATR15"] = _safe_div((ema[15] - ema[50]).abs(), atr[15])
    low50 = df["low"].rolling(50).min()
    high50 = df["high"].rolling(50).max()
    f["Breakout_Position_50"] = _safe_div(df["close"] - low50, high50 - low50)
    f["ATR_Ratio_5_50"] = _safe_div(atr[5], atr[50])
    f["ATR_Ratio_15_200"] = _safe_div(atr[15], atr[200])
    f["ATR_Acceleration_5_15"] = _safe_div(atr[5], atr[15]) - 1
    f["ZScore_Spread_5_50"] = zscore[5] - zscore[50]
    f["ZScore_Spread_15_200"] = zscore[15] - zscore[200]
    f["BBWidth_to_ATR15"] = _safe_div(bb_width, atr[15])
    for period in (5, 15, 50):
        f[f"VolAdj_Return_{period}"] = _safe_div(returns[period], 100 * _safe_div(atr[period], df["close"]))
    f["Downside_Upside_Vol_Ratio_50"] = _down_up_vol_ratio(one_bar_return, 50)
    f["Volume_Ratio_5_50"] = _safe_div(df["volume"].rolling(5).mean(), df["volume"].rolling(50).mean())
    f["Volume_Ratio_15_200"] = _safe_div(df["volume"].rolling(15).mean(), df["volume"].rolling(200).mean())
    f["Price_Volume_Confirm_5"] = np.sign(returns[5]) * f["Volume_Ratio_5_50"]
    f["Price_Volume_Confirm_15"] = np.sign(returns[15]) * f["Volume_Ratio_15_200"]
    f["CMF_Return_Interaction_15"] = cmf20 * returns[15]
    f["CLV_Volume_Interaction"] = f["Close_Location_Value_CLV"] * f["Volume_vs_SMA_50_Pct"]
    f["OBV_Normalized_15"] = _safe_div(obv - obv.shift(15), df["volume"].rolling(15).sum())
    f["OBV_Normalized_50"] = _safe_div(obv - obv.shift(50), df["volume"].rolling(50).sum())
    taker_imbalance = _safe_div(2 * df["taker_buy_base_volume"] - df["volume"], df["volume"])
    f["Taker_Buy_Imbalance"] = taker_imbalance
    f["Taker_Imbalance_Change_15"] = taker_imbalance - taker_imbalance.shift(15)
    avg_trade_size = _safe_div(df["quote_volume"], df["trade_count"])
    f["Avg_Trade_Size_vs_SMA50_Pct"] = _pct_ratio(avg_trade_size, avg_trade_size.rolling(50).mean())
    f["Avg_Trade_Size_Trend_15_Pct"] = avg_trade_size.pct_change(15) * 100
    f["Signed_Body_Pct"] = 100 * _safe_div(df["close"] - df["open"], candle_range)
    f["Wick_Imbalance_Pct"] = 100 * _safe_div(lower_wick - upper_wick, candle_range)
    f["Rejection_Strength"] = _safe_div(lower_wick - upper_wick, candle_range)
    f["Range_vs_ATR15"] = _safe_div(candle_range, atr[15])
    f["Open_Gap_Pct"] = 100 * (_safe_div(df["open"], df["close"].shift(1)) - 1)
    f["Inside_Bar"] = ((df["high"] <= df["high"].shift(1)) & (df["low"] >= df["low"].shift(1))).astype(float)
    f["Outside_Bar"] = ((df["high"] >= df["high"].shift(1)) & (df["low"] <= df["low"].shift(1))).astype(float)
    f["Range_Compression_20"] = _safe_div(candle_range, candle_range.rolling(20).mean())
    f["RSI_Spread_5_50"] = rsi[5] - rsi[50]
    f["RSI_Spread_15_200"] = rsi[15] - rsi[200]
    f["RSI_Slope_Acceleration"] = rsi_slope5 - rsi_slope15
    f["RSI_EMA_Trend_Interaction"] = (rsi14 - 50) * np.sign(ema[5] - ema[50])
    f["MACD_Stoch_Agreement"] = np.sign(macd_hist) * np.sign(stoch_k - stoch_d)
    f["MeanReversion_Trend_Tension"] = -zscore[50] * np.sign(ema[15] - ema[50])
    f["Alt_vs_BTC_Ret_5"] = returns[5] - btc_returns[5]
    f["Alt_vs_BTC_Ret_15"] = returns[15] - btc_returns[15]
    asset_return = df["close"].pct_change()
    btc_one_return = btc_close.pct_change()
    f["BTC_Corr_50"] = asset_return.rolling(50).corr(btc_one_return)
    f["BTC_Beta_200"] = _safe_div(
        asset_return.rolling(200).cov(btc_one_return),
        btc_one_return.rolling(200).var(ddof=1),
    )
    f["Quote_Volume_Market_Share"] = _safe_div(
        df["quote_volume"], market_quote_total.reindex(df.index)
    )

    ordered = list(feature_order)
    missing_columns = [name for name in ordered if name not in f.columns]
    if missing_columns:
        raise ValueError(f"Feature implementation missing: {missing_columns}")
    last = f[ordered].iloc[-1].replace([np.inf, -np.inf], np.nan).astype("float32")
    return FeatureResult(
        values=last,
        missing_count=int(last.isna().sum()),
        approximate_count=len(PIPELINE_DEFINED | UNDER_SPECIFIED),
    )
