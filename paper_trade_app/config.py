from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


APP_DIR = Path(__file__).resolve().parent
WORKSPACE_DIR = APP_DIR.parent
PACKAGE_DIR = (
    WORKSPACE_DIR
    / "PAPER_TRADE_POSITIVE_MODELS_1M_EXECUTION"
    / "paper_trade_package"
)


def _load_dotenv(path: Path) -> None:
    if not path.exists():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


_load_dotenv(APP_DIR / ".env")
_load_dotenv(WORKSPACE_DIR / ".env")


def _as_bool(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class Settings:
    host: str = os.getenv("PT_HOST", "0.0.0.0")
    port: int = int(os.getenv("PT_PORT", "8765"))
    dashboard_user: str = os.getenv("PT_DASHBOARD_USER", "admin")
    dashboard_password: str = os.getenv("PT_DASHBOARD_PASSWORD", "change-me")
    dashboard_auth_enabled: bool = _as_bool("PT_DASHBOARD_AUTH_ENABLED", False)
    live_enabled: bool = _as_bool("PT_LIVE_ENABLED", True)
    legacy_demo_enabled: bool = _as_bool("PT_LEGACY_DEMO_ENABLED", False)
    motorlab_demo_enabled: bool = _as_bool("PT_MOTORLAB_DEMO_ENABLED", False)
    motorlab_demo_accounts_raw: str = os.getenv(
        "PT_MOTORLAB_DEMO_ACCOUNTS", "futures_10x"
    )
    motorlab_demo_margin_usdt: float = float(os.getenv("PT_MOTORLAB_DEMO_MARGIN_USDT", "10"))
    approximate_features: bool = _as_bool("PT_APPROXIMATE_FEATURES", True)
    warmup_bars: int = int(os.getenv("PT_WARMUP_BARS", "1000"))
    full_history_enabled: bool = _as_bool("PT_FULL_HISTORY_ENABLED", True)
    history_start_utc: str = os.getenv("PT_HISTORY_START_UTC", "2022-08-01T00:00:00Z")
    history_db_path: Path = Path(os.getenv(
        "PT_HISTORY_DB_PATH", str(APP_DIR / "data" / "candle_history.db")
    ))
    request_concurrency: int = int(os.getenv("PT_REQUEST_CONCURRENCY", "12"))
    initial_cash_tl: float = float(os.getenv("PT_INITIAL_CASH_TL", "100000"))
    trade_notional_tl: float = float(os.getenv("PT_TRADE_NOTIONAL_TL", "10000"))
    db_path: Path = Path(os.getenv("PT_DB_PATH", str(APP_DIR / "data" / "paper_trade.db")))
    log_path: Path = Path(os.getenv("PT_LOG_PATH", str(APP_DIR / "logs" / "service.log")))
    package_dir: Path = Path(os.getenv("PT_PACKAGE_DIR", str(PACKAGE_DIR)))
    binance_rest_url: str = os.getenv("PT_BINANCE_REST_URL", "https://fapi.binance.com")
    binance_ws_url: str = os.getenv(
        "PT_BINANCE_WS_URL", "wss://fstream.binance.com/market/stream"
    )
    candle_grace_seconds: int = int(os.getenv("PT_CANDLE_GRACE_SECONDS", "15"))
    entry_max_lag_seconds: int = int(os.getenv("PT_ENTRY_MAX_LAG_SECONDS", "180"))
    data_gap_warning_bars: int = int(os.getenv("PT_DATA_GAP_WARNING_BARS", "10"))
    data_integrity_silent_pairs: str = os.getenv(
        "PT_DATA_INTEGRITY_SILENT_PAIRS",
        "4h:PONSUSDT,4h:MARSCOINUSDT,4h:牛来USDT",
    )
    incremental_shadow_hours: float = float(os.getenv("PT_INCREMENTAL_SHADOW_HOURS", "3"))
    max_event_rows: int = int(os.getenv("PT_MAX_EVENT_ROWS", "2000"))
    spot_demo_rest_url: str = os.getenv("PT_SPOT_DEMO_REST_URL", "https://demo-api.binance.com")
    futures_demo_rest_url: str = os.getenv("PT_FUTURES_DEMO_REST_URL", "https://demo-fapi.binance.com")
    binance_api_key: str = os.getenv("BINANCE_API_KEY", "")
    binance_api_secret: str = os.getenv("BINANCE_API_SECRET", "")
    futures_2x_api_key: str = os.getenv("BINANCE_FUTURES_2X_API_KEY", "")
    futures_2x_api_secret: str = os.getenv("BINANCE_FUTURES_2X_API_SECRET", "")
    futures_5x_api_key: str = os.getenv("BINANCE_FUTURES_5X_API_KEY", "")
    futures_5x_api_secret: str = os.getenv("BINANCE_FUTURES_5X_API_SECRET", "")
    futures_10x_api_key: str = os.getenv("BINANCE_FUTURES_10X_API_KEY", os.getenv("BINANCE_API_KEY", ""))
    futures_10x_api_secret: str = os.getenv("BINANCE_FUTURES_10X_API_SECRET", os.getenv("BINANCE_API_SECRET", ""))
    active_scenario_ids_raw: str = os.getenv(
        "PT_ACTIVE_SCENARIO_IDS", "5,6,9,10,15,16,17,19,20,21,49,50,56,64,79,80,108,109,111"
    )
    spot_demo_scenario_ids: str = os.getenv("PT_SPOT_DEMO_SCENARIO_IDS", "")
    spot_demo_quote_usdt: float = float(os.getenv("PT_SPOT_DEMO_QUOTE_USDT", "10"))
    futures_demo_quote_usdt: float = float(os.getenv("PT_FUTURES_DEMO_QUOTE_USDT", "10"))
    execution_account_margin_usdt: float = float(
        os.getenv("PT_EXECUTION_ACCOUNT_MARGIN_USDT", "10")
    )
    execution_account_initial_equity: float = float(
        os.getenv("PT_EXECUTION_ACCOUNT_INITIAL_EQUITY", "1000")
    )
    leverage_paper_margin_usdt: float = float(os.getenv("PT_LEVERAGE_PAPER_MARGIN_USDT", "10"))
    leverage_paper_fee_rate: float = float(os.getenv("PT_LEVERAGE_PAPER_FEE_RATE", "0.0005"))
    leverage_paper_maintenance_rate: float = float(os.getenv("PT_LEVERAGE_PAPER_MAINTENANCE_RATE", "0.005"))

    def mirrored_scenario_ids(self) -> set[int]:
        return {int(value.strip()) for value in self.spot_demo_scenario_ids.split(",") if value.strip()}

    def active_scenario_ids(self) -> set[int]:
        return {
            int(value.strip())
            for value in self.active_scenario_ids_raw.split(",")
            if value.strip()
        }

    def motorlab_demo_accounts(self) -> set[str]:
        return {
            value.strip() for value in self.motorlab_demo_accounts_raw.split(",")
            if value.strip()
        }

    def silent_integrity_pairs(self) -> set[tuple[str, str]]:
        pairs: set[tuple[str, str]] = set()
        for value in self.data_integrity_silent_pairs.split(","):
            timeframe, separator, symbol = value.strip().partition(":")
            if separator and timeframe and symbol:
                pairs.add((timeframe, symbol))
        return pairs


TIMEFRAME_MINUTES = {"1m": 1, "5m": 5, "15m": 15, "1h": 60, "4h": 240}
SIGNAL_TIMEFRAMES = ("5m", "15m", "1h", "4h")


