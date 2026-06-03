import json
import logging
import os
from datetime import datetime, timezone

import azure.functions as func
import requests


app = func.FunctionApp(http_auth_level=func.AuthLevel.FUNCTION)

GRAPH_ROOT = "https://graph.microsoft.com/v1.0"
TABLE_NAME = os.environ.get("EXCEL_TABLE_NAME", "WorkTracker")


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
        append_excel_row(values)
    except requests.HTTPError as error:
        logging.exception("Microsoft Graph request failed")
        response_text = error.response.text if error.response is not None else str(error)
        return json_response({"error": "Excel append failed.", "detail": response_text}, 502)
    except Exception as error:
        logging.exception("Unexpected save-entry failure")
        return json_response({"error": "Save failed.", "detail": str(error)}, 500)

    return json_response({"ok": True, "created_at": created_at}, 200)


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
