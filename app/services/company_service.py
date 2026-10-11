from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
import math
from threading import Lock
import time

import yfinance as yf
from sqlalchemy import asc

from app.domain.episode_data import upsert_episodes_for_company
from app.models import Company, Episode
from app.domain.episode_definition import build_signal_v2_mode, validate_episode_params


@dataclass
class PriceRow:
    date: object
    open: float
    high: float
    low: float
    close: float


PRICE_HISTORY_CACHE_TTL_SECONDS = 15 * 60
MIN_PRICE_LOOKBACK_DAYS = 30
MAX_PRICE_LOOKBACK_DAYS = 365 * 20
CHART_LOOKBACK_PRESETS = {
    "1y": 365,
    "3y": 365 * 3,
    "5y": 365 * 5,
    "10y": 365 * 10,
    "max": None,
}

_price_history_cache: dict[tuple[str, int | str], tuple[float, list[PriceRow]]] = {}
_price_history_lock = Lock()


_SP500_TOP20 = [
    "NVDA", "AAPL", "MSFT", "AMZN", "GOOGL", "GOOG", "AVGO", "META", "TSLA", "BRK-B",
    "WMT", "LLY", "JPM", "V", "ORCL", "XOM", "JNJ", "MA", "COST", "PLTR",
]

_NIFTY50_TOP20 = [
    "RELIANCE.NS", "TCS.NS", "HDFCBANK.NS", "ICICIBANK.NS", "INFY.NS",
    "BHARTIARTL.NS", "SBIN.NS", "LICI.NS", "ITC.NS", "HINDUNILVR.NS",
    "LT.NS", "BAJFINANCE.NS", "MARUTI.NS", "HCLTECH.NS", "SUNPHARMA.NS",
    "KOTAKBANK.NS", "TITAN.NS", "ADANIENT.NS", "ONGC.NS", "NTPC.NS",
]


def _ordered_companies_for_tickers(tickers: list[str]) -> list[Company]:
    rows = Company.query.filter(Company.ticker.in_(tickers)).all()
    by_ticker = {c.ticker: c for c in rows}
    ordered = []
    for ticker in tickers:
        company = by_ticker.get(ticker)
        if company is not None:
            ordered.append(company)
    return ordered


def get_home_companies():
    """Kept for backward compatibility with any existing callers —
    returns the combined flat list (SP500 + NIFTY50)."""
    return _ordered_companies_for_tickers(_SP500_TOP20 + _NIFTY50_TOP20)


def get_home_companies_by_section() -> dict[str, list[Company]]:
    """Returns companies grouped by market, for sectioned homepage display."""
    return {
        "sp500": _ordered_companies_for_tickers(_SP500_TOP20),
        "nifty50": _ordered_companies_for_tickers(_NIFTY50_TOP20),
    }

    rows = Company.query.filter(Company.ticker.in_(top20)).all()
    by_ticker = {c.ticker: c for c in rows}

    ordered = []
    for ticker in top20:
        company = by_ticker.get(ticker)
        if company is not None:
            ordered.append(company)

    return ordered


def get_company_by_ticker(ticker: str):
    return Company.query.filter(Company.ticker == ticker.upper()).first()


def parse_chart_lookback(raw_value: str | None, default: str = "1y") -> int | None:
    """Parse chart-only lookback controls without touching episode parameters."""
    raw = (raw_value or default).strip().lower()
    if raw in CHART_LOOKBACK_PRESETS:
        return CHART_LOOKBACK_PRESETS[raw]

    try:
        days = int(raw)
    except (TypeError, ValueError):
        return CHART_LOOKBACK_PRESETS[default]

    return max(MIN_PRICE_LOOKBACK_DAYS, min(days, MAX_PRICE_LOOKBACK_DAYS))


def get_company_prices(company: Company, lookback_days: int | None = 365) -> list[PriceRow]:
    """Fetch OHLC price history from yfinance with a short backend cache."""
    if lookback_days is None:
        cache_key = (company.ticker.upper(), "max")
    else:
        lookback_days = max(
            MIN_PRICE_LOOKBACK_DAYS,
            min(int(lookback_days), MAX_PRICE_LOOKBACK_DAYS),
        )
        cache_key = (company.ticker.upper(), lookback_days)
    now = time.time()

    with _price_history_lock:
        cached = _price_history_cache.get(cache_key)
        if cached and now - cached[0] < PRICE_HISTORY_CACHE_TTL_SECONDS:
            return list(cached[1])

    try:
        ticker = yf.Ticker(company.ticker)
        if lookback_days is None:
            hist = ticker.history(period="max")
        else:
            end = datetime.utcnow().date()
            start = end - timedelta(days=lookback_days)
            hist = ticker.history(
                start=start.isoformat(),
                end=end.isoformat(),
            )
    except Exception:
        return []

    if hist.empty:
        return []


    def _clean(value) -> float:
        try:
            f = float(value)
        except (TypeError, ValueError):
            return 0.0
        return 0.0 if math.isnan(f) else f

    rows: list[PriceRow] = []
    for ts, row in hist.iterrows():
        d = ts.date() if hasattr(ts, "date") else ts
        rows.append(PriceRow(
            date=d,
            open=_clean(row.get("Open")),
            high=_clean(row.get("High")),
            low=_clean(row.get("Low")),
            close=_clean(row.get("Close")),
        ))

    with _price_history_lock:
        _price_history_cache[cache_key] = (now, list(rows))
    return rows


def get_company_episodes(
    company: Company,
    lookback_days: int = 365,
    mode: str | None = None,
    threshold: float | None = None,
):
    one_year_ago = datetime.utcnow().date() - timedelta(days=lookback_days)
    query = Episode.query.filter(
        Episode.company_id == company.id,
        Episode.window_start >= one_year_ago,
    )
    if mode is not None:
        query = query.filter(Episode.mode == mode)
    if threshold is not None:
        query = query.filter(Episode.threshold == threshold)
    return query.order_by(Episode.peak_date.desc()).all()


def ensure_company_episodes_signal_v2(
    company: Company,
    *,
    horizon_n: int,
    threshold: float,
    merge_gap_days: int,
    lookback_days: int,
) -> tuple[str, int]:
    params = validate_episode_params(
        horizon_n=horizon_n,
        threshold=threshold,
        merge_gap_days=merge_gap_days,
        lookback_days=lookback_days,
    )
    mode = build_signal_v2_mode(params.horizon_n, params.merge_gap_days)
    created = upsert_episodes_for_company(
        company=company,
        mode="signal_v2",
        threshold=params.threshold,
        lookback_days=params.lookback_days,
        horizon_n=params.horizon_n,
        merge_gap_days=params.merge_gap_days,
    )
    return mode, created


def get_episode_for_company(company: Company, episode_id: int):
    episode = Episode.query.get(episode_id)
    if episode is None or episode.company_id != company.id:
        return None
    return episode
