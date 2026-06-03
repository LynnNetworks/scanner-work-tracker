import json
import logging
import os
import uuid
from csv import writer
from datetime import datetime, timezone
from html import escape
from io import StringIO

import azure.functions as func
import requests
from azure.data.tables import TableServiceClient


app = func.FunctionApp(http_auth_level=func.AuthLevel.FUNCTION)

GRAPH_ROOT = "https://graph.microsoft.com/v1.0"
TABLE_NAME = os.environ.get("EXCEL_TABLE_NAME", "WorkTracker")
STORAGE_TABLE_NAME = os.environ.get("STORAGE_TABLE_NAME", "WorkTracker")
READ_ACCESS_TOKEN = os.environ.get(
    "READ_ACCESS_TOKEN",
    "d4de2f6c8f2d4e3f9ac9c2e9b46f3a22",
)


@app.route(route="save-entry", methods=["POST"])
def save_entry(req: func.HttpRequest) -> func.HttpResponse:
    try:
        payload = req.get_json()
    except ValueError:
        return json_response({"error": "Invalid JSON body."}, 400)

    position_id = str(payload.get("position_id", "")).strip()
    payroll_name = str(payload.get("payroll_name", "")).strip()
    batch_id = str(payload.get("batch_id", "")).strip()
    tablet_id = str(payload.get("tablet_id", "")).strip()

    if not position_id:
        return json_response({"error": "position_id is required."}, 400)
    if not batch_id:
        return json_response({"error": "batch_id is required."}, 400)

    created_at = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    values = [[created_at, position_id, payroll_name, batch_id, tablet_id]]

    try:
        if graph_configured():
            append_excel_row(values)
        else:
            append_storage_row(created_at, position_id, payroll_name, batch_id, tablet_id)
    except requests.HTTPError as error:
        logging.exception("Microsoft Graph request failed")
        response_text = error.response.text if error.response is not None else str(error)
        return json_response({"error": "Excel append failed.", "detail": response_text}, 502)
    except Exception as error:
        logging.exception("Unexpected save-entry failure")
        return json_response({"error": "Save failed.", "detail": str(error)}, 500)

    return json_response({"ok": True, "created_at": created_at}, 200)


@app.route(route="entries.csv", methods=["GET"], auth_level=func.AuthLevel.ANONYMOUS)
def entries_csv(req: func.HttpRequest) -> func.HttpResponse:
    if not read_token_valid(req):
        return json_response({"error": "Unauthorized."}, 401)

    try:
        entities = list(get_storage_table().query_entities("PartitionKey eq 'entry'"))
    except Exception as error:
        logging.exception("Could not read storage rows")
        return json_response({"error": "Could not read entries.", "detail": str(error)}, 500)

    entities.sort(key=lambda row: row.get("CreatedAt", ""))
    buffer = StringIO()
    csv_writer = writer(buffer)
    csv_writer.writerow(["Created At", "Position ID", "Payroll Name", "Batch ID", "Tablet ID"])
    for row in entities:
        csv_writer.writerow(
            [
                row.get("CreatedAt", ""),
                row.get("PositionId", ""),
                row.get("PayrollName", ""),
                row.get("BatchId", ""),
                row.get("TabletId", ""),
            ]
        )

    return func.HttpResponse(
        buffer.getvalue(),
        status_code=200,
        mimetype="text/csv",
    )


@app.route(route="entries.html", methods=["GET"], auth_level=func.AuthLevel.ANONYMOUS)
def entries_html(req: func.HttpRequest) -> func.HttpResponse:
    if not read_token_valid(req):
        return json_response({"error": "Unauthorized."}, 401)

    try:
        entities = list(get_storage_table().query_entities("PartitionKey eq 'entry'"))
    except Exception as error:
        logging.exception("Could not read storage rows")
        return json_response({"error": "Could not read entries.", "detail": str(error)}, 500)

    entities.sort(key=lambda row: row.get("CreatedAt", ""))
    headers = ["Created At", "Position ID", "Payroll Name", "Batch ID", "Tablet ID"]
    rows = [
        [
            row.get("CreatedAt", ""),
            row.get("PositionId", ""),
            row.get("PayrollName", ""),
            row.get("BatchId", ""),
            row.get("TabletId", ""),
        ]
        for row in entities
    ]
    header_html = "".join(f"<th>{escape(header)}</th>" for header in headers)
    rows_html = "".join(
        "<tr>" + "".join(f"<td>{escape(str(value))}</td>" for value in row) + "</tr>"
        for row in rows
    )
    html = (
        "<!doctype html><html><head><meta charset=\"utf-8\">"
        "<title>Scanner Work Tracker</title></head><body>"
        "<table><thead><tr>"
        f"{header_html}"
        "</tr></thead><tbody>"
        f"{rows_html}"
        "</tbody></table></body></html>"
    )
    return func.HttpResponse(html, status_code=200, mimetype="text/html")


def graph_configured():
    return all(
        os.environ.get(name)
        for name in [
            "AZURE_TENANT_ID",
            "AZURE_CLIENT_ID",
            "AZURE_CLIENT_SECRET",
            "EXCEL_DRIVE_ID",
            "EXCEL_ITEM_ID",
        ]
    )


def append_storage_row(created_at, position_id, payroll_name, batch_id, tablet_id):
    table = get_storage_table()
    table.create_entity(
        {
            "PartitionKey": "entry",
            "RowKey": f"{created_at}-{uuid.uuid4()}",
            "CreatedAt": created_at,
            "PositionId": position_id,
            "PayrollName": payroll_name,
            "BatchId": batch_id,
            "TabletId": tablet_id,
        }
    )


def get_storage_table():
    connection_string = required_setting("AzureWebJobsStorage")
    service = TableServiceClient.from_connection_string(connection_string)
    return service.create_table_if_not_exists(STORAGE_TABLE_NAME)


def read_token_valid(req: func.HttpRequest):
    return req.params.get("token") == READ_ACCESS_TOKEN


def append_excel_row(values):
    token = get_graph_token()
    drive_id = required_setting("EXCEL_DRIVE_ID")
    item_id = required_setting("EXCEL_ITEM_ID")
    url = (
        f"{GRAPH_ROOT}/drives/{drive_id}/items/{item_id}"
        f"/workbook/tables/{TABLE_NAME}/rows/add"
    )
    response = requests.post(
        url,
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        },
        json={"values": values},
        timeout=30,
    )
    response.raise_for_status()


def get_graph_token():
    tenant_id = required_setting("AZURE_TENANT_ID")
    client_id = required_setting("AZURE_CLIENT_ID")
    client_secret = required_setting("AZURE_CLIENT_SECRET")
    response = requests.post(
        f"https://login.microsoftonline.com/{tenant_id}/oauth2/v2.0/token",
        data={
            "client_id": client_id,
            "client_secret": client_secret,
            "scope": "https://graph.microsoft.com/.default",
            "grant_type": "client_credentials",
        },
        timeout=30,
    )
    response.raise_for_status()
    return response.json()["access_token"]


def required_setting(name):
    value = os.environ.get(name)
    if not value:
        raise RuntimeError(f"Missing app setting: {name}")
    return value


def json_response(body, status_code):
    return func.HttpResponse(
        json.dumps(body),
        status_code=status_code,
        mimetype="application/json",
    )
