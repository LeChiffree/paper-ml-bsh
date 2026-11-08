from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import joblib
import numpy as np
import pandas as pd


TARGET_RE = re.compile(r"(?:sonuc|future_range|realized_volatility)_(\d+)mum")


@dataclass
class ScenarioSpec:
    scenario_id: int
    timeframe: str
    scenario: str
    strategy: str
    target: str
    filter_name: str
    threshold: float | None
    horizon: int
    tp_pct: float
    sl_pct: float
    vol_low: float
    vol_high: float
    enabled: bool
    blocked_reason: str | None


class ModelRegistry:
    def __init__(self, package_dir: Path):
        self.package_dir = package_dir
        self.feature_order: list[str] = json.loads(
            (package_dir / "feature_order.json").read_text(encoding="utf-8")
        )
        manifest = pd.read_csv(package_dir / "model_manifest.csv")
        self.models: dict[tuple[str, str], dict[str, Any]] = {}
        self.load_seconds = 0.0
        started = time.perf_counter()
        for row in manifest.itertuples(index=False):
            artifact = joblib.load(package_dir / row.relative_path)
            model = artifact["model"]
            names = list(model.feature_name_)
            if names != self.feature_order + ["symbol"]:
                raise ValueError(f"Feature order mismatch: {row.relative_path}")
            self.models[(row.timeframe, row.target)] = artifact
        self.load_seconds = time.perf_counter() - started
        artifact_symbols = set().union(*(set(item["symbol_map"]) for item in self.models.values()))
        universe_path = Path(__file__).resolve().with_name("coin_universe.json")
        configured_symbols = json.loads(universe_path.read_text(encoding="utf-8"))
        if len(configured_symbols) != 106 or len(set(configured_symbols)) != 106:
            raise ValueError("coin_universe.json must contain 106 unique symbols")
        if set(configured_symbols) != artifact_symbols:
            missing = sorted(artifact_symbols - set(configured_symbols))
            extra = sorted(set(configured_symbols) - artifact_symbols)
            raise ValueError(f"Coin universe/model mismatch; missing={missing}, extra={extra}")
        self.symbols = configured_symbols

    def has_model(self, timeframe: str, target: str) -> bool:
        return (timeframe, target) in self.models

    @staticmethod
    def _horizon_ten_fallback_targets(target: str) -> tuple[str, str] | None:
        """Return the explicitly approved h5/h15 regression proxy for h10."""
        if target == "future_range_10mum_pct":
            return ("future_range_5mum_pct", "future_range_15mum_pct")
        if target == "realized_volatility_10mum_pct":
            return (
                "realized_volatility_5mum_pct",
                "realized_volatility_15mum_pct",
            )
        return None

    def can_predict(self, timeframe: str, target: str) -> bool:
        if self.has_model(timeframe, target):
            return True
        fallback = self._horizon_ten_fallback_targets(target)
        return bool(
            fallback
            and all(self.has_model(timeframe, item) for item in fallback)
        )

    def predict(
        self, timeframe: str, target: str, features: pd.DataFrame
    ) -> dict[str, Any]:
        if not self.has_model(timeframe, target):
            fallback = self._horizon_ten_fallback_targets(target)
            if not fallback or not all(
                self.has_model(timeframe, item) for item in fallback
            ):
                raise KeyError(f"Model is unavailable: {timeframe} {target}")
            lower = self.predict(timeframe, fallback[0], features)
            upper = self.predict(timeframe, fallback[1], features)
            return {
                symbol: (float(lower[symbol]) + float(upper[symbol])) / 2.0
                for symbol in lower.keys() & upper.keys()
            }
        artifact = self.models[(timeframe, target)]
        supported = [s for s in features.index if s in artifact["symbol_map"]]
        if not supported:
            return {}
        matrix = features.loc[supported, self.feature_order].astype("float32")
        matrix = matrix.replace([np.inf, -np.inf], np.nan)
        codes = pd.Series(
            [artifact["symbol_map"][s] for s in supported], index=supported, dtype="int64"
        )
        model = artifact["model"]
        categories = model.booster_.pandas_categorical[0]
        matrix["symbol"] = pd.Categorical(codes, categories=categories)
        if "classes" in artifact:
            probabilities = model.predict_proba(matrix)
            classes = list(artifact["classes"])
            tp_idx = classes.index("TP")
            best = np.argmax(probabilities, axis=1)
            return {
                symbol: {
                    "prediction": classes[int(best[idx])],
                    "tp_score": float(probabilities[idx, tp_idx]),
                }
                for idx, symbol in enumerate(supported)
            }
        values = model.predict(matrix)
        return {symbol: float(values[idx]) for idx, symbol in enumerate(supported)}


def parse_horizon(target: str) -> int:
    match = TARGET_RE.search(target)
    if not match:
        raise ValueError(f"Cannot parse target horizon: {target}")
    return int(match.group(1))


def load_scenarios(package_dir: Path, registry: ModelRegistry) -> list[ScenarioSpec]:
    book = package_dir / "BACKTEST_POSITIVE_METHODS_1M_EXECUTION.xlsx"
    summary = pd.read_excel(book, sheet_name="Pozitif_Sonuclar")
    details = pd.read_excel(
        book,
        sheet_name="Detayli_Islemler",
        usecols=["scenario_id", "horizon_4h", "tp_pct", "sl_pct", "vol_low", "vol_high"],
    )
    constants = details.groupby("scenario_id", as_index=False).first()
    summary = summary.merge(constants, on="scenario_id", how="left")
    specs: list[ScenarioSpec] = []
    for row in summary.itertuples(index=False):
        filter_name = str(row.filter)
        horizon = int(row.horizon_4h)
        reasons: list[str] = []
        if not registry.has_model(row.timeframe, row.target):
            reasons.append("classification_model_missing")
        if filter_name in {"range_full", "range_half_volatility", "range_full_volatility"}:
            if not registry.can_predict(row.timeframe, f"future_range_{horizon}mum_pct"):
                reasons.append("range_model_missing")
        if filter_name in {"volatility", "range_half_volatility", "range_full_volatility"}:
            if not registry.can_predict(row.timeframe, f"realized_volatility_{horizon}mum_pct"):
                reasons.append("volatility_model_missing")
        specs.append(
            ScenarioSpec(
                scenario_id=int(row.scenario_id),
                timeframe=str(row.timeframe),
                scenario=str(row.scenario),
                strategy=str(row.strategy),
                target=str(row.target),
                filter_name=filter_name,
                threshold=None if pd.isna(row.threshold) else float(row.threshold),
                horizon=horizon,
                tp_pct=float(row.tp_pct),
                sl_pct=float(row.sl_pct),
                vol_low=float(row.vol_low),
                vol_high=float(row.vol_high),
                enabled=not reasons,
                blocked_reason=",".join(reasons) if reasons else None,
            )
        )
    return specs
