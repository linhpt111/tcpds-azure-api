# TPC-DS Sales API

FastAPI service for serving TPC-DS sales data from Azure Blob Storage.

CSV files are not committed to GitHub. They must already exist in Azure Blob
Storage.

## Repository Structure

```text
tpcds-api/
|
|-- main.py
|-- requirements.txt
|-- .gitignore
`-- README.md
```

## Required Blob Files

Upload these CSV files to the same Blob container:

```text
date_dim.csv
web_sales.csv
store_sales.csv
catalog_sales.csv
inventory.csv
```

If the files are inside a folder in the container, set `AZURE_BLOB_PREFIX`.

## Environment Variables

| Name | Required | Example |
| --- | --- | --- |
| `AZURE_STORAGE_CONNECTION_STRING` | Yes | `DefaultEndpointsProtocol=...` |
| `AZURE_BLOB_CONTAINER` | Yes | `tpcds-data` |
| `CONTAINER_NAME` | No | `tpcds-data` |
| `AZURE_BLOB_PREFIX` | No | `csv_output` |
| `BLOB_CACHE_DIR` | No | `/tmp/tpcds-api-cache` |

## Run Locally

```bat
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
```

Set environment variables in CMD:

```bat
set AZURE_STORAGE_CONNECTION_STRING=your_connection_string
set AZURE_BLOB_CONTAINER=your_container_name
set AZURE_BLOB_PREFIX=csv_output
```

Start the API:

```bat
python -m uvicorn main:app --host 127.0.0.1 --port 9000 --reload
```

## Deploy To Azure App Service

Use Azure App Service Linux with Python.

Startup Command:

```bash
gunicorn -w 2 -k uvicorn.workers.UvicornWorker -b 0.0.0.0:8000 main:app --timeout 600
```

Set the same environment variables in Azure App Service:

```text
AZURE_STORAGE_CONNECTION_STRING
AZURE_BLOB_CONTAINER
AZURE_BLOB_PREFIX
```

`CONTAINER_NAME` is also supported as a fallback for `AZURE_BLOB_CONTAINER`.

## Endpoints

```text
GET /
GET /health
GET /api/v1/sales/web?start_date=2026-07-06&limit=2
GET /api/v1/sales/store?start_date=2026-07-06&limit=2
GET /api/v1/sales/catalog?start_date=2026-07-06&limit=2
GET /api/v1/sales/inventory?start_date=2026-07-06&limit=2
```

`inventory` is snapshot-style data, so not every date has matching rows. If a
specific date returns `count: 0`, try a wider `start_date` range.

## Success Response

```json
{
  "status": "success",
  "channel": "web",
  "count": 2,
  "start_date": "06/07/2026",
  "end_date": "06/07/2026",
  "date_column": "sold_date",
  "date_format": "dd/mm/yyyy",
  "limit": 2,
  "truncated": true,
  "data": [
    {
      "ws_sold_date_sk": 2461228,
      "sold_date": "06/07/2026",
      "ws_sold_time_sk": "83199",
      "ws_item_sk": "13303"
    }
  ]
}
```
