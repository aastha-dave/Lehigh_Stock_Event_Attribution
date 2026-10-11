from __future__ import annotations

from dataclasses import dataclass
from io import StringIO

import pandas as pd
import requests

from app.models import Company, db

NIFTY50_CSV_URL = "https://nsearchives.nseindia.com/content/indices/ind_nifty50list.csv"

# NSE blocks requests that don't look like they're coming from a real
# browser. A plain requests.get() with no headers will get rejected, so
# we first "visit" the homepage to pick up valid cookies, then download
# the CSV using the same session (this mirrors how a browser behaves).
_BROWSER_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": "https://www.nseindia.com/market-data/live-equity-market",
}


@dataclass(frozen=True)
class Nifty50IngestResult:
    inserted: int
    updated: int
    existing_count: int
    fetched_count: int
    skipped: bool = False


def fetch_nifty50_df() -> pd.DataFrame:
    """Fetch the NIFTY 50 constituent list from the official NSE CSV."""
    with requests.Session() as session:
        session.headers.update(_BROWSER_HEADERS)

        # Warm-up request: sets cookies NSE expects before it will serve
        # the CSV. Failures here are ignored -- some environments already
        # succeed without it, and the real download attempt below will
        # raise a clear error if something is still wrong.
        try:
            session.get("https://www.nseindia.com", timeout=10)
        except requests.RequestException:
            pass

        response = session.get(NIFTY50_CSV_URL, timeout=15)
        response.raise_for_status()

    df = pd.read_csv(StringIO(response.content.decode("utf-8")))

    col_map = {}
    for col in df.columns:
        lower = col.strip().lower()
        if "symbol" in lower:
            col_map[col] = "ticker"
        elif "company" in lower:
            col_map[col] = "name"
        elif "industry" in lower:
            col_map[col] = "gics_sector"

    df = df.rename(columns=col_map)

    needed = ["ticker", "name", "gics_sector"]
    for col in needed:
        if col not in df.columns:
            df[col] = None
    df = df[needed].copy()
    df["gics_sub_industry"] = None

    df["ticker"] = df["ticker"].str.strip()
    df["name"] = df["name"].str.strip()
    df = df.dropna(subset=["ticker"])

    # Yahoo Finance requires the ".NS" suffix for stocks traded on the
    # National Stock Exchange of India. Without it, price lookups for
    # these tickers would fail or pull the wrong (US-listed) security.
    df["ticker"] = df["ticker"] + ".NS"

    return df


def ingest_nifty50_companies(
    *,
    dry_run: bool = False,
    only_if_empty: bool = False,
    verbose: bool = True,
) -> Nifty50IngestResult:
    """
    Upsert NIFTY 50 company master data into the bound Flask database.

    Call this inside an app context. When only_if_empty is true, the
    network fetch is skipped if the companies table already has rows.
    Mirrors ingest_sp500_companies() in sp500_ingestion.py so both
    markets are ingested the same way.
    """
    db.create_all()

    existing_count = Company.query.count()
    if only_if_empty and existing_count > 0:
        if verbose:
            print(f"[startup] Company data already available ({existing_count} rows).")
        return Nifty50IngestResult(
            inserted=0,
            updated=0,
            existing_count=existing_count,
            fetched_count=0,
            skipped=True,
        )

    if verbose:
        print("Fetching NIFTY 50 company list from NSE...")

    df = fetch_nifty50_df()

    if verbose:
        print(f"  Found {len(df)} companies.")

    inserted = 0
    updated = 0

    for _, row in df.iterrows():
        ticker = str(row["ticker"]).upper()
        name = str(row["name"])
        gics_sector = row["gics_sector"] if pd.notna(row["gics_sector"]) else None
        gics_sub_industry = (
            row["gics_sub_industry"] if pd.notna(row["gics_sub_industry"]) else None
        )

        if dry_run:
            if verbose:
                print(f"  [DRY RUN] {ticker} | {name} | {gics_sector} | {gics_sub_industry}")
            inserted += 1
            continue

        existing = Company.query.filter_by(ticker=ticker).first()
        if existing is None:
            db.session.add(Company(
                ticker=ticker,
                name=name,
                gics_sector=gics_sector,
                gics_sub_industry=gics_sub_industry,
            ))
            inserted += 1
            continue

        dirty = False
        if existing.name != name:
            existing.name = name
            dirty = True
        if existing.gics_sector != gics_sector:
            existing.gics_sector = gics_sector
            dirty = True
        if existing.gics_sub_industry != gics_sub_industry:
            existing.gics_sub_industry = gics_sub_industry
            dirty = True
        if dirty:
            updated += 1

    if not dry_run:
        db.session.commit()

    if verbose:
        print(f"Done. inserted={inserted} updated={updated}")

    return Nifty50IngestResult(
        inserted=inserted,
        updated=updated,
        existing_count=existing_count,
        fetched_count=len(df),
        skipped=False,
    )


def ensure_nifty50_companies(verbose: bool = True) -> Nifty50IngestResult:
    return ingest_nifty50_companies(
        dry_run=False,
        only_if_empty=False,
        verbose=verbose,
    )