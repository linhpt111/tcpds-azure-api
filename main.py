import logging
import os
import tempfile
from datetime import date, datetime
from pathlib import Path

import pandas as pd
from azure.storage.blob import BlobServiceClient
from fastapi import FastAPI, HTTPException, Query


logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI(title="TPC-DS Sales API")

CONNECTION_STRING = os.getenv("AZURE_STORAGE_CONNECTION_STRING")
CONTAINER_NAME = os.getenv("AZURE_BLOB_CONTAINER") or os.getenv("CONTAINER_NAME")
BLOB_PREFIX = os.getenv("AZURE_BLOB_PREFIX", "").strip("/")
CACHE_DIR = Path(os.getenv("BLOB_CACHE_DIR", Path(tempfile.gettempdir()) / "tpcds-api-cache"))

DATE_DIM_BLOB = "date_dim.csv"
DATA_FILES = {
    "web": ("web_sales.csv", "ws_sold_date_sk", "sold_date"),
    "store": ("store_sales.csv", "ss_sold_date_sk", "sold_date"),
    "catalog": ("catalog_sales.csv", "cs_sold_date_sk", "sold_date"),
    "inventory": ("inventory.csv", "inv_date_sk", "inventory_date"),
}

date_dim_cache = None


def require_blob_config():
    if not CONNECTION_STRING:
        raise HTTPException(status_code=500, detail="Missing AZURE_STORAGE_CONNECTION_STRING")
    if not CONTAINER_NAME:
        raise HTTPException(status_code=500, detail="Missing AZURE_BLOB_CONTAINER")


def blob_name(file_name):
    return f"{BLOB_PREFIX}/{file_name}" if BLOB_PREFIX else file_name


def get_blob_service_client():
    require_blob_config()
    return BlobServiceClient.from_connection_string(CONNECTION_STRING)


def download_blob_to_cache(file_name):
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    local_path = CACHE_DIR / file_name
    if local_path.exists() and local_path.stat().st_size > 0:
        return local_path

    service = get_blob_service_client()
    blob_client = service.get_blob_client(container=CONTAINER_NAME, blob=blob_name(file_name))

    logger.info("Downloading blob %s to %s", blob_name(file_name), local_path)
    try:
        with local_path.open("wb") as file:
            stream = blob_client.download_blob()
            for chunk in stream.chunks():
                file.write(chunk)
    except Exception as exc:
        local_path.unlink(missing_ok=True)
        raise HTTPException(status_code=500, detail=f"Failed to download {file_name}: {exc}") from exc

    return local_path


@app.on_event("startup")
async def startup_event():
    global date_dim_cache
    try:
        date_dim_path = download_blob_to_cache(DATE_DIM_BLOB)
        df = pd.read_csv(date_dim_path, usecols=["d_date", "d_date_sk"], encoding="utf-8-sig")
        date_dim_cache = dict(zip(df["d_date"].astype(str), df["d_date_sk"].astype(int)))
        logger.info("Date dimension loaded: %s rows", len(date_dim_cache))
    except Exception as exc:
        logger.error("Failed to load date dimension: %s", exc, exc_info=True)
        date_dim_cache = None


def build_date_mapping(start_date_str):
    if date_dim_cache is None:
        raise HTTPException(status_code=500, detail="Date dimension is not loaded")

    try:
        start = datetime.strptime(start_date_str, "%Y-%m-%d").date()
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Invalid date format. Use YYYY-MM-DD") from exc

    today = date.today()
    if start > today:
        start = today

    current_dates = pd.date_range(start=start, end=today)
    current_date_strings = current_dates.strftime("%Y-%m-%d").tolist()
    past_date_strings = (current_dates - pd.DateOffset(years=25)).strftime("%Y-%m-%d").tolist()

    mapping = {}
    for current_date, past_date in zip(current_date_strings, past_date_strings):
        current_sk = date_dim_cache.get(current_date)
        past_sk = date_dim_cache.get(past_date)
        if current_sk is not None and past_sk is not None:
            mapping[str(past_sk)] = {
                "date_sk": int(current_sk),
                "display_date": datetime.strptime(current_date, "%Y-%m-%d").strftime("%d/%m/%Y"),
            }

    return start, today, mapping


def get_rows(file_name, sk_column, date_column, start_date, limit):
    start, end, past_to_current = build_date_mapping(start_date)
    past_sk_set = set(past_to_current)

    rows = []
    truncated = False

    if not past_sk_set:
        return {
            "records": rows,
            "start_date": start.strftime("%d/%m/%Y"),
            "end_date": end.strftime("%d/%m/%Y"),
            "truncated": truncated,
        }

    local_path = download_blob_to_cache(file_name)
    logger.info("Reading %s", local_path)

    for chunk in pd.read_csv(
        local_path,
        dtype=str,
        keep_default_na=False,
        encoding="utf-8-sig",
        chunksize=100000,
    ):
        matching = chunk[chunk[sk_column].isin(past_sk_set)].copy()
        if matching.empty:
            continue

        original_sk = matching[sk_column].astype(str)
        matching[sk_column] = original_sk.map(lambda sk: past_to_current[sk]["date_sk"])
        matching.insert(
            matching.columns.get_loc(sk_column) + 1,
            date_column,
            original_sk.map(lambda sk: past_to_current[sk]["display_date"]),
        )

        remaining = limit - len(rows)
        if len(matching) > remaining:
            rows.extend(matching.head(remaining).to_dict(orient="records"))
            truncated = True
            break

        rows.extend(matching.to_dict(orient="records"))
        if len(rows) >= limit:
            truncated = True
            break

    return {
        "records": rows,
        "start_date": start.strftime("%d/%m/%Y"),
        "end_date": end.strftime("%d/%m/%Y"),
        "truncated": truncated,
    }


def get_channel_rows(channel, start_date, limit):
    if channel not in DATA_FILES:
        raise HTTPException(status_code=404, detail=f"Unknown channel: {channel}")

    file_name, sk_column, date_column = DATA_FILES[channel]
    return get_rows(file_name, sk_column, date_column, start_date, limit)


def build_response(channel, result, limit):
    _, _, date_column = DATA_FILES[channel]
    return {
        "status": "success",
        "channel": channel,
        "count": len(result["records"]),
        "start_date": result["start_date"],
        "end_date": result["end_date"],
        "date_column": date_column,
        "date_format": "dd/mm/yyyy",
        "limit": limit,
        "truncated": result["truncated"],
        "data": result["records"],
    }


@app.get("/")
async def root():
    return {
        "title": "TPC-DS Sales API",
        "status": "running" if date_dim_cache else "initializing",
        "endpoints": [
            "/api/v1/sales/web",
            "/api/v1/sales/store",
            "/api/v1/sales/catalog",
            "/api/v1/sales/inventory",
        ],
    }


@app.get("/health")
async def health():
    return {
        "status": "healthy" if date_dim_cache else "initializing",
        "date_dim_loaded": date_dim_cache is not None,
        "blob_container": CONTAINER_NAME,
        "blob_prefix": BLOB_PREFIX,
    }


@app.get("/api/v1/sales/{channel}")
async def sales(
    channel: str,
    start_date: str = Query(..., description="Start date in YYYY-MM-DD format"),
    limit: int = Query(1000, ge=1, le=10000, description="Maximum rows to return"),
):
    result = get_channel_rows(channel, start_date, limit)
    return build_response(channel, result, limit)
