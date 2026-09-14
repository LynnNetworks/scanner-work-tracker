import json
import logging
import os
import secrets
import uuid
from csv import writer
from datetime import datetime, timezone
from html import escape
from io import StringIO

import azure.functions as func
import requests
from azure.data.tables import TableServiceClient


app = func.FunctionApp(http_auth_level=func.AuthLevel.FUNCTION)

STORAGE_TABLE_NAME = os.environ.get("STORAGE_TABLE_NAME", "WorkTracker")
FLOW_PUSH_SYNC_ENABLED = os.environ.get("FLOW_PUSH_SYNC_ENABLED", "").lower() in {
    "1",
    "true",
    "yes",
}
FLOW_PUSH_SYNC_URL = os.environ.get("FLOW_PUSH_SYNC_URL", "").strip()
FLOW_PUSH_SYNC_TIMEOUT_SECONDS = int(os.environ.get("FLOW_PUSH_SYNC_TIMEOUT_SECONDS", "90"))
READ_ACCESS_TOKEN = os.environ.get(
    "READ_ACCESS_TOKEN",
    "",
)
OPERATOR_AREA_PARTITION = "operator_area"

@app.route(route="save-entry", methods=["POST"])
def save_entry(req: func.HttpRequest) -> func.HttpResponse:
    try:
        payload = req.get_json()
    except ValueError:
        return json_response({"error": "Invalid JSON body."}, 400)

    position_id = str(payload.get("position_id", "")).strip()
    payroll_name = str(payload.get("payroll_name", "")).strip()
    area = resolve_operator_area(position_id)
    batch_id = str(payload.get("batch_id", "")).strip()
    tablet_id = str(payload.get("tablet_id", "")).strip()

    if not position_id:
        return json_response({"error": "position_id is required."}, 400)
    if not batch_id:
        return json_response({"error": "batch_id is required."}, 400)
    if not FLOW_PUSH_SYNC_ENABLED:
        return json_response(
            {
                "error": "Power Automate push synchronization is not configured."
            },
            503,
        )

    created_at = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    try:
        row_key = append_storage_row(
            created_at,
            position_id,
            payroll_name,
            area,
            batch_id,
            tablet_id,
        )
        flow_dispatch = dispatch_entry_to_flow(row_key)
    except Exception as error:
        logging.exception("Unexpected save-entry failure")
        return json_response({"error": "Save failed.", "detail": str(error)}, 500)

    return json_response(
        {
            "ok": True,
            "created_at": created_at,
            "schedule_update": {"status": "submitted_to_flow"},
            "flow_dispatch": flow_dispatch,
        },
        200,
    )


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
    csv_writer.writerow(["Created At", "Position ID", "Payroll Name", "Area", "Batch ID", "Tablet ID"])
    for row in entities:
        csv_writer.writerow(
            [
                row.get("CreatedAt", ""),
                row.get("PositionId", ""),
                row.get("PayrollName", ""),
                row.get("Area", "Unassigned"),
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
    headers = ["Created At", "Position ID", "Payroll Name", "Area", "Batch ID", "Tablet ID"]
    rows = [
        [
            row.get("CreatedAt", ""),
            row.get("PositionId", ""),
            row.get("PayrollName", ""),
            row.get("Area", "Unassigned"),
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


@app.route(route="entry-dispatch", methods=["POST"], auth_level=func.AuthLevel.ANONYMOUS)
def entry_dispatch(req: func.HttpRequest) -> func.HttpResponse:
    if not read_token_valid(req):
        return json_response({"error": "Unauthorized."}, 401)
    if not FLOW_PUSH_SYNC_ENABLED:
        return json_response({"error": "Push flow synchronization is not enabled."}, 409)

    try:
        dispatched = dispatch_pending_entries()
    except Exception as error:
        logging.exception("Could not dispatch pending flow entries")
        return json_response({"error": "Could not dispatch pending entries.", "detail": str(error)}, 500)

    return json_response({"ok": True, "dispatched": dispatched}, 200)


@app.schedule(schedule="0 */5 * * * *", arg_name="timer", use_monitor=True)
def retry_pending_flow_entries(timer: func.TimerRequest) -> None:
    if not FLOW_PUSH_SYNC_ENABLED:
        return
    try:
        dispatched = dispatch_pending_entries()
        if dispatched:
            logging.info("Retried %s pending flow entries", dispatched)
    except Exception:
        logging.exception("Could not retry pending flow entries")


@app.route(route="cleanup-test-entries", methods=["POST"], auth_level=func.AuthLevel.ANONYMOUS)
def cleanup_test_entries(req: func.HttpRequest) -> func.HttpResponse:
    if not read_token_valid(req):
        return json_response({"error": "Unauthorized."}, 401)

    try:
        table = get_storage_table()
        entities = table.query_entities("PartitionKey eq 'entry'")
        deleted = 0
        for row in entities:
            if row.get("BatchId") == "TEST123" and row.get("TabletId") == "Codex-Test":
                table.delete_entity(row["PartitionKey"], row["RowKey"])
                deleted += 1
    except Exception as error:
        logging.exception("Could not delete test rows")
        return json_response({"error": "Could not delete test rows.", "detail": str(error)}, 500)

    return json_response({"ok": True, "deleted": deleted}, 200)


@app.route(route="operator-areas", methods=["GET"], auth_level=func.AuthLevel.ANONYMOUS)
def operator_areas(req: func.HttpRequest) -> func.HttpResponse:
    if not read_token_valid(req):
        return json_response({"error": "Unauthorized."}, 401)

    try:
        position_id = normalize_position_id(req.params.get("position_id"))
        if position_id:
            assignment = get_live_operator_assignment(position_id)
            return json_response({"assignment": assignment}, 200)
        rows = list_live_operator_areas()
    except Exception as error:
        logging.exception("Could not read live operator area map")
        return json_response({"error": "Could not read operator areas.", "detail": str(error)}, 500)

    return json_response({"assignments": rows, "count": len(rows)}, 200)


@app.route(route="operator-areas-sync", methods=["POST"], auth_level=func.AuthLevel.ANONYMOUS)
def operator_areas_sync(req: func.HttpRequest) -> func.HttpResponse:
    if not read_token_valid(req):
        return json_response({"error": "Unauthorized."}, 401)

    try:
        payload = req.get_json()
    except ValueError:
        return json_response({"error": "Invalid JSON body."}, 400)

    try:
        assignments = normalize_operator_area_payload(payload)
    except ValueError as error:
        return json_response({"error": str(error)}, 400)
    if not assignments:
        return json_response({"error": "At least one assignment is required."}, 400)

    invalid = [
        item
        for item in assignments
        if not item["payroll_name"]
    ]
    if invalid:
        return json_response(
            {
                "error": "Invalid tracker assignments.",
                "invalid": invalid[:25],
            },
            400,
        )

    try:
        result = replace_live_operator_areas(assignments)
    except Exception as error:
        logging.exception("Could not sync live operator area map")
        return json_response({"error": "Could not sync operator areas.", "detail": str(error)}, 500)

    return json_response({"ok": True, **result}, 200)


def append_storage_row(
    created_at,
    position_id,
    payroll_name,
    area,
    batch_id,
    tablet_id,
):
    table = get_storage_table()
    row_key = f"{created_at}-{uuid.uuid4()}"
    table.create_entity(
        {
            "PartitionKey": "entry",
            "RowKey": row_key,
            "CreatedAt": created_at,
            "PositionId": position_id,
            "PayrollName": payroll_name,
            "Area": area,
            "BatchId": batch_id,
            "TabletId": tablet_id,
            "FlowSyncStatus": "pending",
        }
    )
    return row_key


def dispatch_pending_entries(limit=20):
    table = get_storage_table()
    pending = list(
        table.query_entities(
            "PartitionKey eq 'entry' and FlowSyncStatus eq 'pending'",
            results_per_page=limit,
        )
    )[:limit]
    pending.sort(key=lambda row: row.get("CreatedAt", ""))
    dispatched = 0
    for entry in pending:
        result = dispatch_entry_to_flow(entry["RowKey"], entry=entry)
        if result["status"] == "completed":
            dispatched += 1
    return dispatched


def dispatch_entry_to_flow(row_key, entry=None):
    if not FLOW_PUSH_SYNC_URL:
        raise RuntimeError("Missing app setting: FLOW_PUSH_SYNC_URL")

    if entry is None:
        entry = get_storage_table().get_entity("entry", row_key)
    if entry.get("FlowSyncStatus") == "completed":
        return {"status": "already_completed", "row_key": row_key}

    payload = {
        "row_key": row_key,
        "created_at": entry.get("CreatedAt", ""),
        "position_id": entry.get("PositionId", ""),
        "payroll_name": entry.get("PayrollName", ""),
        "area": entry.get("Area", "Unassigned"),
        "batch_id": entry.get("BatchId", ""),
        "tablet_id": entry.get("TabletId", ""),
    }
    try:
        response = requests.post(
            FLOW_PUSH_SYNC_URL,
            json=payload,
            timeout=FLOW_PUSH_SYNC_TIMEOUT_SECONDS,
        )
        response.raise_for_status()
    except requests.RequestException as error:
        logging.warning("Power Automate push failed for entry %s: %s", row_key, error)
        return {"status": "pending_retry", "row_key": row_key, "detail": str(error)}

    entry["FlowSyncStatus"] = "completed"
    entry["FlowSyncCompletedAt"] = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    get_storage_table().update_entity(entry)
    return {"status": "completed", "row_key": row_key}


def get_storage_table():
    connection_string = required_setting("AzureWebJobsStorage")
    service = TableServiceClient.from_connection_string(connection_string)
    return service.create_table_if_not_exists(STORAGE_TABLE_NAME)


def read_token_valid(req: func.HttpRequest):
    token = req.params.get("token", "")
    return bool(READ_ACCESS_TOKEN) and secrets.compare_digest(token, READ_ACCESS_TOKEN)


def resolve_operator_area(position_id):
    normalized_position_id = normalize_position_id(position_id)
    found_live_area, live_area = get_live_operator_area(normalized_position_id)
    if found_live_area:
        return live_area or "Unassigned"
    return "Unassigned"


def normalize_position_id(position_id):
    return str(position_id or "").strip().upper()


def get_live_operator_area(position_id):
    if not position_id:
        return False, ""

    try:
        entity = get_storage_table().get_entity(OPERATOR_AREA_PARTITION, position_id)
        return True, str(entity.get("Area", "")).strip()
    except Exception as error:
        status_code = getattr(error, "status_code", None)
        if status_code == 404:
            return False, ""
        logging.warning("Could not read live operator area for %s: %s", position_id, error)
        return False, ""


def get_live_operator_assignment(position_id):
    try:
        entity = get_storage_table().get_entity(OPERATOR_AREA_PARTITION, position_id)
    except Exception as error:
        if getattr(error, "status_code", None) == 404:
            return None
        raise
    return {
        "position_id": position_id,
        "payroll_name": str(entity.get("PayrollName", "")).strip(),
        "area": str(entity.get("Area", "")).strip(),
        "updated_at": entity.get("UpdatedAt", ""),
    }


def list_live_operator_areas():
    try:
        entities = get_storage_table().query_entities(f"PartitionKey eq '{OPERATOR_AREA_PARTITION}'")
        rows = []
        for row in entities:
            position_id = normalize_position_id(row.get("RowKey", ""))
            if position_id:
                rows.append(
                    {
                        "position_id": position_id,
                        "payroll_name": row.get("PayrollName", ""),
                        "area": row.get("Area", ""),
                        "updated_at": row.get("UpdatedAt", ""),
                    }
                )
    except Exception as error:
        logging.warning("Could not read tracker operator areas: %s", error)
        raise

    rows.sort(key=lambda item: (item["payroll_name"], item["position_id"]))
    return rows


def normalize_operator_area_payload(payload):
    if isinstance(payload, dict) and isinstance(payload.get("assignments"), list):
        source_rows = payload["assignments"]
    elif isinstance(payload, dict) and isinstance(payload.get("areas"), dict):
        source_rows = [
            {"position_id": position_id, "area": area}
            for position_id, area in payload["areas"].items()
        ]
    elif isinstance(payload, list):
        source_rows = payload
    else:
        raise ValueError("Expected assignments array, areas object, or array body.")

    assignments = []
    seen = set()
    for row in source_rows:
        if not isinstance(row, dict):
            continue
        position_id = normalize_position_id(
            row.get("position_id")
            or row.get("Position ID")
            or row.get("PositionId")
        )
        if not position_id or position_id in seen:
            continue
        payroll_name = str(
            row.get("payroll_name")
            or row.get("Payroll Name")
            or row.get("PayrollName")
            or ""
        ).strip()
        area = str(row.get("area") or row.get("Area") or "").strip().lower()
        assignments.append(
            {
                "position_id": position_id,
                "payroll_name": payroll_name,
                "area": area,
            }
        )
        seen.add(position_id)

    return assignments


def replace_live_operator_areas(assignments):
    table = get_storage_table()
    existing = {
        row["RowKey"]
        for row in table.query_entities(f"PartitionKey eq '{OPERATOR_AREA_PARTITION}'")
    }
    incoming = {item["position_id"] for item in assignments}
    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")

    upserted = 0
    for item in assignments:
        table.upsert_entity(
            {
                "PartitionKey": OPERATOR_AREA_PARTITION,
                "RowKey": item["position_id"],
                "PayrollName": item["payroll_name"],
                "Area": item["area"],
                "UpdatedAt": timestamp,
            }
        )
        upserted += 1

    deleted = 0
    for row_key in existing - incoming:
        table.delete_entity(OPERATOR_AREA_PARTITION, row_key)
        deleted += 1

    assigned = sum(1 for item in assignments if item["area"])
    return {
        "upserted": upserted,
        "deleted": deleted,
        "assigned": assigned,
        "blank": upserted - assigned,
        "updated_at": timestamp,
    }


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
