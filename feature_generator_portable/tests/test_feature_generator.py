import json
from pathlib import Path

import numpy as np
import pandas as pd

from feature_engine import FEATURE_ORDER, compute_latest_matrix


def candles(symbol_shift=0.0, count=300):
    t=np.arange(count,dtype="int64")*300_000+1_700_000_000_000
    close=100+symbol_shift+np.arange(count)*0.03+np.sin(np.arange(count)/7)
    return pd.DataFrame({"open_time":t,"open":close-.1,"high":close+.5,"low":close-.5,"close":close,
        "volume":1000+np.arange(count),"close_time":t+299_999,"quote_volume":close*(1000+np.arange(count)),
        "trade_count":100+np.arange(count)%20,"taker_buy_base_volume":500.,
        "taker_buy_quote_volume":close*500,"ignore":0.})


def test_exact_contract():
    frames={"BTCUSDT":candles(),"ETHUSDT":candles(50),"ADAUSDT":candles(-99)}
    matrix,_=compute_latest_matrix(frames,int(frames["BTCUSDT"].close_time.max())+1)
    assert len(FEATURE_ORDER)==132
    assert list(matrix[FEATURE_ORDER+["symbol"]].columns)==FEATURE_ORDER+["symbol"]
    assert set(matrix.symbol)==set(frames)
    assert np.isfinite(matrix.loc["ADAUSDT","Return_5m"])
