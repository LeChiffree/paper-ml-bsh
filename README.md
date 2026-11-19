# Paper ML BSH

Machine-learning paper-trading system for Binance Futures market data. The
repository contains the live paper engine, dashboard, feature generator,
deployment helpers, and the packaged LightGBM model artifacts used by the
runtime.

## Current paper scope

- Universe: 106 symbols
- Timeframes: 15m, 1h, and 4h for the currently selected scenarios
- Active scenario IDs: `5, 9, 10, 15, 19, 49, 56, 64, 79, 108, 111`
- Simulated accounts: spot 1x, futures 2x, futures 5x, futures 10x
- Margin per signal/account: 10 USDT
- Live Binance order execution is disabled by default

## Main directories

- `paper_trade_app/`: FastAPI service, paper engine, dashboard, and tests
- `feature_generator_portable/`: portable 132-feature generator
- `PAPER_TRADE_POSITIVE_MODELS_1M_EXECUTION/`: model package and backtest source
- `scripts/`: replay, recovery, reconciliation, and benchmark tools
- `deploy/`: deployment examples

See `paper_trade_app/README.md` and `PAPER_TRADING_SYSTEM_TRANSFER.md` for the
detailed operational documentation.

The complete ID/method/filter/algorithm reference is in
`SYSTEM_METHODS_SCENARIOS_ALGORITHMS.md`. It contains all 219 scenario IDs,
their coded names, thresholds, filters, horizons, model targets, backtest
statistics, the 132-feature inventory, and the paper execution algorithm.

## Backtest dashboard

The repository includes a standalone read-only dashboard for the packaged
backtest workbook:

```bash
uvicorn paper_trade_app.backtest_dashboard:app --host 0.0.0.0 --port 8766
```

Open `http://127.0.0.1:8766/`. The dashboard exposes the 219-scenario catalogue,
timeframe aggregates, current active-11 filter, and paginated trade detail API.
It does not start the live paper engine or submit orders.

## Singular-Backtest (tek açık işlem)

[`Singular-Backtest/`](Singular-Backtest/README.md) applies a one-open-trade
rule to the packaged backtest: for each scenario and coin, a new signal is
blocked while a trade is already open. The normal backtest allows stacking
(10,059 of 54,325 signals arrive during an open trade), while the paper-trade
guide asks for one position per coin. The variant reuses the detailed trade
list, so it does not rerun the ML models.

```bash
python Singular-Backtest/run_singular.py
python Singular-Backtest/verify_singular.py
python Singular-Backtest/summarize_singular.py
```

Outputs land in `Singular-Backtest/results/`; the dashboard payload is
`Singular-Backtest/singular_results.json`. Scope can be switched with
`--scope scenario|scenario_name|strategy`.

## Expanded-Backtest (tüm coinler, ilk tarihten)

[`Expanded-Backtest/`](Expanded-Backtest/README.md) reruns the same 219
scenarios on every coin in `D:\1mdatabinance\1m` (526 symbols), starting each
coin at its own first available 1m candle. Higher timeframes are derived from
1m, the packaged 132-feature engine and 83 LightGBM models are reused, and both
the normal 10-slot portfolio and the singular one-position rule are produced.

```bash
python Expanded-Backtest/prepare_context.py
python Expanded-Backtest/run_expanded.py run --workers 8
python Expanded-Backtest/run_expanded.py merge
python Expanded-Backtest/validate.py
```

The dashboard payload is written to `Expanded-Backtest/results/` and can be
published with `Expanded-Backtest/update_dashboard.py` after the merge.

## Security and local state

Secrets and runtime state are intentionally not committed. Create local `.env`
files from the included examples. Databases, logs, backups, generated research
datasets, PDF archives, and transfer archives are excluded by `.gitignore`.

