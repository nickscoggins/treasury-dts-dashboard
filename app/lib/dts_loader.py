from __future__ import annotations

from pathlib import Path
import pandas as pd

REPO_ROOT = Path(__file__).resolve().parents[2]
DATA_RAW = REPO_ROOT / "data" / "raw"


def _find_first_csv(candidates: list[str]) -> Path:
    if not DATA_RAW.exists():
        raise FileNotFoundError(f"Expected folder not found: {DATA_RAW}")

    csvs = list(DATA_RAW.glob("*.csv"))
    if not csvs:
        raise FileNotFoundError(f"No CSV files found in {DATA_RAW}")

    for c in candidates:
        for f in csvs:
            if c.lower() in f.name.lower():
                return f

    raise FileNotFoundError(
        "Could not auto-detect required CSV. "
        f"Looked for {candidates} in filenames under {DATA_RAW}. "
        f"Found: {[f.name for f in csvs]}"
    )


def find_deposits_withdrawals_csv() -> Path:
    return _find_first_csv(["deposits", "withdrawals", "operating cash", "dwoc", "opcash"])


def find_category_map_csv() -> Path:
    return _find_first_csv(["category_map", "cat_map", "mapping", "rollup", "opcash_category_map"])


def load_category_map(path: Path | None = None) -> pd.DataFrame:
    """
    Mapping file must contain at least:
      - transaction_catg
      - transaction_catg_desc
      - cabinet_supercategory
      - agency_rollup
      - program_rollup

    It may also contain transaction_type (we'll keep it, but we won't join on it).
    """
    path = path or find_category_map_csv()
    m = pd.read_csv(path, dtype=str).copy()

    m.columns = [c.strip() for c in m.columns]
    if "transaction_cetg_desc" in m.columns and "transaction_catg_desc" not in m.columns:
        m = m.rename(columns={"transaction_cetg_desc": "transaction_catg_desc"})

    required = {
        "transaction_catg",
        "transaction_catg_desc",
        "cabinet_supercategory",
        "agency_rollup",
        "program_rollup",
    }
    missing = required - set(m.columns)
    if missing:
        raise ValueError(f"Mapping file missing columns: {sorted(missing)}. Found: {list(m.columns)}")

    # Normalize join keys
    m["transaction_catg"] = m["transaction_catg"].astype("string").str.strip()
    m["transaction_catg_desc"] = m["transaction_catg_desc"].astype("string").str.strip()

    # Normalize rollups
    for col in ["cabinet_supercategory", "agency_rollup", "program_rollup"]:
        m[col] = m[col].astype("string").str.strip()

    # If transaction_type exists, normalize it too (not used in join but useful for QA)
    if "transaction_type" in m.columns:
        m["transaction_type"] = m["transaction_type"].astype("string").str.strip()

    # Optional: drop exact duplicate mappings on the join keys (keeps merge deterministic)
    m = m.drop_duplicates(subset=["transaction_catg", "transaction_catg_desc"], keep="first")

    return m


def load_deposits_withdrawals(path: Path | None = None) -> pd.DataFrame:
    path = path or find_deposits_withdrawals_csv()
    df = pd.read_csv(path).copy()

    required = {
        "record_date",
        "account_type",
        "transaction_type",
        "transaction_catg",
        "transaction_catg_desc",
        "transaction_today_amt",
    }
    missing = required - set(df.columns)
    if missing:
        raise ValueError(f"Deposits/Withdrawals CSV missing columns: {sorted(missing)}")

    df["record_date"] = pd.to_datetime(df["record_date"], errors="coerce")
    df = df[df["record_date"].notna()].copy()

    df["transaction_today_amt"] = pd.to_numeric(df["transaction_today_amt"], errors="coerce").fillna(0.0)

    # Amounts are in millions -> dollars
    df["transaction_today_amt"] = df["transaction_today_amt"] * 1_000_000

    for c in ["account_type", "transaction_type", "transaction_catg", "transaction_catg_desc"]:
        df[c] = df[c].astype("string").str.strip()

    # Exclude TGA total lines entirely (they'll otherwise double-count flows)
    df = df[~df["account_type"].isin([
        "Treasury General Account Total Deposits",
        "Treasury General Account Total Withdrawals",
    ])].copy()

    df = df[df["transaction_today_amt"] != 0].copy()
    return df


def enrich_with_rollups(df: pd.DataFrame, mapping: pd.DataFrame) -> pd.DataFrame:
    """
    Left-join mapping on (transaction_catg, transaction_catg_desc).
    """
    out = df.merge(
        mapping[
            [
                "transaction_catg",
                "transaction_catg_desc",
                "cabinet_supercategory",
                "agency_rollup",
                "program_rollup",
            ]
        ],
        on=["transaction_catg", "transaction_catg_desc"],
        how="left",
    )

    out["cabinet_supercategory"] = out["cabinet_supercategory"].fillna("Unmapped")
    out["agency_rollup"] = out["agency_rollup"].fillna("Unmapped")
    out["program_rollup"] = out["program_rollup"].fillna("Unmapped")

    return out
