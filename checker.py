import pandas as pd
import numpy as np
import sqlite3
import json
from datetime import datetime


# ─────────────────────────────────────────────
# INGESTION — Mode 1 (CSV) and Mode 2 (SQLite)
# ─────────────────────────────────────────────

def load_csv(filepath: str) -> pd.DataFrame:
    """Mode 1: Load data from a CSV file."""
    try:
        df = pd.read_csv(filepath, low_memory=False)
        return df
    except FileNotFoundError:
        raise FileNotFoundError(f"CSV file not found: {filepath}")
    except pd.errors.EmptyDataError:
        raise ValueError("The CSV file is empty.")


def load_from_db(db_path: str, table_name: str) -> pd.DataFrame:
    """Mode 2: Load data from a SQLite database table."""
    try:
        conn = sqlite3.connect(db_path)
        # Verify the table exists
        cursor = conn.cursor()
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table'")
        tables = [row[0] for row in cursor.fetchall()]
        if table_name not in tables:
            conn.close()
            raise ValueError(
                f"Table '{table_name}' not found in database. "
                f"Available tables: {tables}"
            )
        df = pd.read_sql_query(f"SELECT * FROM {table_name}", conn)
        conn.close()
        return df
    except sqlite3.OperationalError as e:
        raise ConnectionError(f"Could not connect to database '{db_path}': {e}")


def get_db_tables(db_path: str) -> list:
    """Helper: return all table names in a SQLite database."""
    try:
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table'")
        tables = [row[0] for row in cursor.fetchall()]
        conn.close()
        return tables
    except Exception:
        return []


def load_data(source: str, table_name: str = None) -> pd.DataFrame:
    """
    Unified loader — dispatches to CSV or SQLite based on file extension.
    source     : path to .csv  →  Mode 1
    source     : path to .db or .sqlite  →  Mode 2  (table_name required)
    """
    ext = source.lower().rsplit(".", 1)[-1]
    if ext == "csv":
        return load_csv(source)
    elif ext in ("db", "sqlite", "sqlite3"):
        if not table_name:
            raise ValueError(
                "table_name is required when source is a database file."
            )
        return load_from_db(source, table_name)
    else:
        raise ValueError(
            f"Unsupported file type '.{ext}'. Use .csv or .db/.sqlite"
        )


def get_dataset_profile(df: pd.DataFrame, source: str, table_name: str = None) -> dict:
    """Capture metadata about the loaded dataset for the LLM context."""
    return {
        "source": source,
        "table": table_name or "N/A (CSV)",
        "row_count": len(df),
        "column_count": len(df.columns),
        "columns": list(df.columns),
        "dtypes": {col: str(df[col].dtype) for col in df.columns},
        "memory_usage_kb": round(df.memory_usage(deep=True).sum() / 1024, 2),
        "sample_rows": df.head(3).fillna("NULL").to_dict(orient="records"),
    }


# ─────────────────────────────────────────────
# QUALITY CHECKS
# ─────────────────────────────────────────────

def check_nulls(df: pd.DataFrame) -> dict:
    """Check for null / missing values per column."""
    null_counts = df.isnull().sum()
    null_pct = (null_counts / len(df) * 100).round(2)
    affected = {
        col: {
            "null_count": int(null_counts[col]),
            "null_percent": float(null_pct[col]),
        }
        for col in df.columns
        if null_counts[col] > 0
    }
    return {
        "check": "null_values",
        "status": "FAIL" if affected else "PASS",
        "total_nulls": int(null_counts.sum()),
        "affected_columns": affected,
    }


def check_duplicates(df: pd.DataFrame) -> dict:
    """Check for fully duplicated rows."""
    dup_count = int(df.duplicated().sum())
    dup_pct = round(dup_count / len(df) * 100, 2)
    sample = (
        df[df.duplicated(keep=False)]
        .head(3)
        .fillna("NULL")
        .to_dict(orient="records")
        if dup_count > 0
        else []
    )
    return {
        "check": "duplicates",
        "status": "FAIL" if dup_count > 0 else "PASS",
        "duplicate_row_count": dup_count,
        "percent_of_data": dup_pct,
        "sample_duplicates": sample,
    }


def check_outliers(df: pd.DataFrame) -> dict:
    """Detect outliers in numeric columns using the IQR method."""
    numeric_cols = df.select_dtypes(include=[np.number]).columns.tolist()
    outlier_info = {}

    for col in numeric_cols:
        series = df[col].dropna()
        if len(series) < 4:
            continue
        Q1 = series.quantile(0.25)
        Q3 = series.quantile(0.75)
        IQR = Q3 - Q1
        lower = Q1 - 1.5 * IQR
        upper = Q3 + 1.5 * IQR
        outliers = series[(series < lower) | (series > upper)]
        if len(outliers) > 0:
            outlier_info[col] = {
                "outlier_count": int(len(outliers)),
                "expected_range": f"{round(lower, 2)} – {round(upper, 2)}",
                "outlier_values": [round(v, 2) for v in outliers.tolist()],
            }

    return {
        "check": "outliers",
        "status": "FAIL" if outlier_info else "PASS",
        "affected_columns": outlier_info,
    }


def check_schema(df: pd.DataFrame, expected_schema: dict = None) -> dict:
    """
    Detect schema drift — columns whose dtype differs from expectation.
    If no expected_schema is passed, the current schema is recorded
    as the baseline (useful for first-run detection).
    """
    actual_schema = {col: str(df[col].dtype) for col in df.columns}

    if expected_schema is None:
        return {
            "check": "schema_drift",
            "status": "PASS",
            "note": "No expected schema provided — current schema recorded as baseline.",
            "current_schema": actual_schema,
            "drifted_columns": {},
        }

    drift = {
        col: {
            "expected": expected_schema.get(col, "not_expected"),
            "actual": actual_schema.get(col, "missing"),
        }
        for col in set(list(expected_schema.keys()) + list(actual_schema.keys()))
        if actual_schema.get(col) != expected_schema.get(col)
    }

    return {
        "check": "schema_drift",
        "status": "WARN" if drift else "PASS",
        "drifted_columns": drift,
        "current_schema": actual_schema,
    }


def check_date_validity(df: pd.DataFrame) -> dict:
    """Check object columns that look like dates for unparseable values."""
    date_cols = [
        col for col in df.select_dtypes(include="object").columns
        if "date" in col.lower() or "time" in col.lower()
    ]
    invalid = {}
    for col in date_cols:
        bad_values = []
        for val in df[col].dropna():
            try:
                pd.to_datetime(val)
            except Exception:
                bad_values.append(str(val))
        if bad_values:
            invalid[col] = {
                "invalid_count": len(bad_values),
                "sample_invalid_values": bad_values[:5],
            }

    return {
        "check": "date_validity",
        "status": "FAIL" if invalid else "PASS",
        "affected_columns": invalid,
    }


# ─────────────────────────────────────────────
# MAIN RUNNER
# ─────────────────────────────────────────────

def run_all_checks(
    source: str,
    table_name: str = None,
    expected_schema: dict = None,
) -> tuple[dict, pd.DataFrame]:
    """
    Entry point. Loads data (CSV or SQLite) then runs all checks.
    Returns (results_dict, dataframe).
    """
    df = load_data(source, table_name)
    profile = get_dataset_profile(df, source, table_name)

    checks = [
        check_nulls(df),
        check_duplicates(df),
        check_outliers(df),
        check_schema(df, expected_schema),
        check_date_validity(df),
    ]

    statuses = [c["status"] for c in checks]
    if "FAIL" in statuses:
        overall = "FAIL"
    elif "WARN" in statuses:
        overall = "WARN"
    else:
        overall = "PASS"

    results = {
        "run_timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "dataset_profile": profile,
        "overall_status": overall,
        "checks": checks,
    }

    with open("results.json", "w") as f:
        json.dump(results, f, indent=2, default=str)

    return results, df
