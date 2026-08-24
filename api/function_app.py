import base64
import json
import logging
import os
import re
import secrets
import uuid
from csv import writer
from datetime import datetime, timezone
from email.utils import format_datetime
from html import escape
from io import StringIO
from urllib.parse import quote

import azure.functions as func
import requests
from azure.data.tables import TableServiceClient


app = func.FunctionApp(http_auth_level=func.AuthLevel.FUNCTION)

GRAPH_ROOT = "https://graph.microsoft.com/v1.0"
TABLE_NAME = os.environ.get("EXCEL_TABLE_NAME", "WorkTracker")
STORAGE_TABLE_NAME = os.environ.get("STORAGE_TABLE_NAME", "WorkTracker")
SCHEDULE_SHEET_NAME = os.environ.get("SCHEDULE_SHEET_NAME", "PA Fiber")
SCHEDULE_HEADER_ROW = int(os.environ.get("SCHEDULE_HEADER_ROW", "3"))
SCHEDULE_FIRST_DATA_ROW = int(os.environ.get("SCHEDULE_FIRST_DATA_ROW", "4"))
SCHEDULE_LAST_DATA_ROW = int(os.environ.get("SCHEDULE_LAST_DATA_ROW", "358"))
SCHEDULE_JOB_HEADER = os.environ.get("SCHEDULE_JOB_HEADER", "Job #")
SCHEDULE_ENABLED = os.environ.get("SCHEDULE_ENABLED", "").lower() in {"1", "true", "yes"}
FLOW_SCHEDULE_QUEUE_ENABLED = os.environ.get("FLOW_SCHEDULE_QUEUE_ENABLED", "").lower() in {
    "1",
    "true",
    "yes",
}
READ_ACCESS_TOKEN = os.environ.get(
    "READ_ACCESS_TOKEN",
    "",
)
OPERATOR_AREA_PARTITION = "operator_area"

AREA_COLUMN_MAP = {
    "cut": "Cut",
    "prep": "Prep",
    "term": "Terminated",
    "terminated": "Terminated",
    "polish": "Polish",
    "scope": "Scope",
    "test": "Test",
    "pack": "In Pack-Ship",
    "pack-ship": "In Pack-Ship",
}


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
    if not graph_configured():
        return json_response(
            {
                "error": (
                    "Direct workbook storage is not configured. Set EXCEL_SITE_PATH, "
                    "EXCEL_FILE_PATH, EXCEL_TABLE_NAME, and Microsoft Graph authentication."
                )
            },
            503,
        )

    created_at = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    values = [[created_at, position_id, payroll_name, area, batch_id, tablet_id]]

    try:
        append_excel_row(values)
    except requests.HTTPError as error:
        logging.exception("Microsoft Graph request failed")
        response_text = error.response.text if error.response is not None else str(error)
        return json_response({"error": "Excel append failed.", "detail": response_text}, 502)
    except Exception as error:
        logging.exception("Unexpected save-entry failure")
        return json_response({"error": "Save failed.", "detail": str(error)}, 500)

    if FLOW_SCHEDULE_QUEUE_ENABLED:
        schedule_update = queue_status_for_scan(area, batch_id)
    else:
        schedule_update = update_schedule_from_scan(area, batch_id)

    return json_response({"ok": True, "created_at": created_at, "schedule_update": schedule_update}, 200)


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


@app.route(route="schedule-pending", methods=["GET"], auth_level=func.AuthLevel.ANONYMOUS)
def schedule_pending(req: func.HttpRequest) -> func.HttpResponse:
    if not read_token_valid(req):
        return json_response({"error": "Unauthorized."}, 401)

    try:
        entities = get_storage_table().query_entities("PartitionKey eq 'entry'")
        pending = []
        for row in entities:
            if row.get("ScheduleStatus") != "pending":
                continue
            parsed = parse_barcode(str(row.get("BatchId", "")))
            schedule_column = schedule_column_for_area(row.get("Area", ""))
            if not parsed or not schedule_column:
                continue
            pending.append(
                {
                    "row_key": row["RowKey"],
                    "created_at": row.get("CreatedAt", ""),
                    "job": parsed["job"],
                    "quantity": parsed["conn_qty"],
                    "column": schedule_column,
                }
            )
        pending.sort(key=lambda row: row["created_at"])
    except Exception as error:
        logging.exception("Could not read pending schedule updates")
        return json_response({"error": "Could not read pending schedule updates.", "detail": str(error)}, 500)

    return json_response({"updates": pending}, 200)


@app.route(route="schedule-next", methods=["GET"], auth_level=func.AuthLevel.ANONYMOUS)
def schedule_next(req: func.HttpRequest) -> func.HttpResponse:
    if not read_token_valid(req):
        return json_response({"error": "Unauthorized."}, 401)

    try:
        entities = list(get_storage_table().query_entities("PartitionKey eq 'entry'"))
        candidates = []
        for row in entities:
            parsed = parse_barcode(str(row.get("BatchId", "")))
            schedule_column = schedule_column_for_area(row.get("Area", ""))
            if not parsed or not schedule_column:
                continue
            candidates.append((row, parsed, schedule_column))

        candidates.sort(key=lambda item: item[0].get("CreatedAt", ""))
        for row, parsed, schedule_column in candidates:
            if row.get("ScheduleStatus") == "pending":
                return json_response(
                    {
                        "row_keys": [row["RowKey"]],
                        "job": parsed["job"],
                        "quantity": parsed["conn_qty"],
                        "column": schedule_column,
                        "noop": False,
                    },
                    200,
                )

        if candidates:
            row, parsed, _ = candidates[-1]
            return json_response(
                {
                    "row_keys": [],
                    "job": parsed["job"],
                    "quantity": 0,
                    "column": "",
                    "noop": True,
                },
                200,
            )
    except Exception as error:
        logging.exception("Could not read next schedule update")
        return json_response({"error": "Could not read next schedule update.", "detail": str(error)}, 500)

    return json_response(
        {
            "row_keys": [],
            "job": "327111",
            "quantity": 0,
            "column": "",
            "noop": True,
        },
        200,
    )


@app.route(route="schedule-feed", methods=["GET"], auth_level=func.AuthLevel.ANONYMOUS)
def schedule_feed(req: func.HttpRequest) -> func.HttpResponse:
    if not read_token_valid(req):
        return json_response({"error": "Unauthorized."}, 401)

    try:
        entities = get_storage_table().query_entities("PartitionKey eq 'entry'")
        items = []
        for row in entities:
            if row.get("ScheduleStatus") != "pending":
                continue
            parsed = parse_barcode(str(row.get("BatchId", "")))
            schedule_column = schedule_column_for_area(row.get("Area", ""))
            if not parsed or not schedule_column:
                continue
            created_at = datetime.strptime(
                row.get("CreatedAt", ""),
                "%Y-%m-%d %H:%M:%S UTC",
            ).replace(tzinfo=timezone.utc)
            payload = json.dumps(
                {
                    "row_key": row["RowKey"],
                    "job": parsed["job"],
                    "quantity": parsed["conn_qty"],
                    "column": schedule_column,
                },
                separators=(",", ":"),
            )
            items.append(
                "<item>"
                f"<title>{escape(parsed['job'])} - {escape(schedule_column)}</title>"
                f"<guid isPermaLink=\"false\">{escape(row['RowKey'])}</guid>"
                f"<pubDate>{format_datetime(created_at)}</pubDate>"
                f"<description>{escape(payload)}</description>"
                "</item>"
            )
        items.reverse()
    except Exception as error:
        logging.exception("Could not build schedule feed")
        return json_response({"error": "Could not build schedule feed.", "detail": str(error)}, 500)

    xml = (
        "<?xml version=\"1.0\" encoding=\"UTF-8\"?>"
        "<rss version=\"2.0\"><channel>"
        "<title>Scanner Schedule Updates</title>"
        "<link>https://scanner-work-tracker-egenova-bjg0faecehdre2d7.canadacentral-01.azurewebsites.net</link>"
        "<description>Qualifying scanner entries awaiting schedule updates.</description>"
        + "".join(items)
        + "</channel></rss>"
    )
    return func.HttpResponse(xml, status_code=200, mimetype="application/rss+xml")


@app.route(route="schedule-ack", methods=["POST"], auth_level=func.AuthLevel.ANONYMOUS)
def schedule_ack(req: func.HttpRequest) -> func.HttpResponse:
    if not read_token_valid(req):
        return json_response({"error": "Unauthorized."}, 401)

    try:
        payload = req.get_json()
        row_keys = payload.get("row_keys", [])
        if not isinstance(row_keys, list):
            return json_response({"error": "row_keys must be an array."}, 400)

        table = get_storage_table()
        acknowledged = 0
        for row_key in row_keys:
            entity = table.get_entity("entry", str(row_key))
            entity["ScheduleStatus"] = "applied"
            entity["ScheduleAppliedAt"] = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
            table.update_entity(entity)
            acknowledged += 1
    except Exception as error:
        logging.exception("Could not acknowledge schedule updates")
        return json_response({"error": "Could not acknowledge schedule updates.", "detail": str(error)}, 500)

    return json_response({"ok": True, "acknowledged": acknowledged}, 200)


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
        or (item["area"] and schedule_column_for_area(item["area"]) is None)
    ]
    if invalid:
        return json_response(
            {
                "error": "Invalid tracker assignments.",
                "allowed_prefixes": sorted(AREA_COLUMN_MAP.keys()),
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


def graph_configured():
    user_drive_target_configured = all(
        os.environ.get(name) for name in ["EXCEL_USER_PRINCIPAL", "EXCEL_FILE_PATH"]
    )
    path_target_configured = all(
        os.environ.get(name) for name in ["EXCEL_SITE_PATH", "EXCEL_FILE_PATH"]
    )
    direct_target_configured = bool(os.environ.get("EXCEL_FILE_URL", "").strip())
    legacy_target_configured = all(
        os.environ.get(name) for name in ["EXCEL_DRIVE_ID", "EXCEL_ITEM_ID"]
    )
    return graph_auth_configured() and (
        user_drive_target_configured
        or path_target_configured
        or direct_target_configured
        or legacy_target_configured
    )


def schedule_configured():
    direct_item_configured = os.environ.get("SCHEDULE_DRIVE_ID") and os.environ.get("SCHEDULE_ITEM_ID")
    path_configured = os.environ.get("SCHEDULE_SITE_PATH") and os.environ.get("SCHEDULE_FILE_PATH")
    return SCHEDULE_ENABLED and graph_auth_configured() and (direct_item_configured or path_configured)


def graph_auth_configured():
    if os.environ.get("AZURE_USE_MANAGED_IDENTITY", "").lower() == "true":
        return True
    return all(
        os.environ.get(name)
        for name in [
            "AZURE_TENANT_ID",
            "AZURE_CLIENT_ID",
            "AZURE_CLIENT_SECRET",
        ]
    )


def append_storage_row(created_at, position_id, payroll_name, area, batch_id, tablet_id):
    table = get_storage_table()
    schedule_status = "pending" if FLOW_SCHEDULE_QUEUE_ENABLED and is_schedule_candidate(area, batch_id) else "ignored"
    table.create_entity(
        {
            "PartitionKey": "entry",
            "RowKey": f"{created_at}-{uuid.uuid4()}",
            "CreatedAt": created_at,
            "PositionId": position_id,
            "PayrollName": payroll_name,
            "Area": area,
            "BatchId": batch_id,
            "TabletId": tablet_id,
            "ScheduleStatus": schedule_status,
        }
    )


def get_storage_table():
    connection_string = required_setting("AzureWebJobsStorage")
    service = TableServiceClient.from_connection_string(connection_string)
    return service.create_table_if_not_exists(STORAGE_TABLE_NAME)


def read_token_valid(req: func.HttpRequest):
    token = req.params.get("token", "")
    return bool(READ_ACCESS_TOKEN) and secrets.compare_digest(token, READ_ACCESS_TOKEN)


def update_schedule_from_scan(area, batch_id):
    if not schedule_configured():
        return {"status": "disabled"}

    parsed = parse_barcode(batch_id)
    if not parsed:
        logging.info("Schedule update skipped: invalid barcode length")
        return {"status": "skipped", "reason": "invalid_barcode"}

    schedule_column = schedule_column_for_area(area)
    if not schedule_column:
        logging.info("Schedule update skipped: unmapped area %r", area)
        return {"status": "skipped", "reason": "unmapped_area"}

    try:
        result = add_schedule_quantity(parsed["job"], schedule_column, parsed["conn_qty"])
        logging.info("Schedule update result: %s", result)
        return result
    except requests.HTTPError as error:
        response_text = error.response.text if error.response is not None else str(error)
        logging.exception("Schedule update failed")
        return {"status": "error", "detail": response_text}
    except Exception as error:
        logging.exception("Schedule update failed")
        return {"status": "error", "detail": str(error)}


def queue_status_for_scan(area, batch_id):
    if not is_schedule_candidate(area, batch_id):
        return {"status": "skipped", "reason": "invalid_barcode_or_area"}
    return {"status": "queued"}


def is_schedule_candidate(area, batch_id):
    return bool(parse_barcode(batch_id) and schedule_column_for_area(area))


def parse_barcode(batch_id):
    digits = re.sub(r"\D", "", str(batch_id or ""))
    if len(digits) == 13:
        return {
            "job": digits[:6],
            "line": digits[6:8],
            "conn_qty": int(digits[8:11]),
            "batch": digits[11:13],
        }
    if len(digits) == 14:
        return {
            "job": digits[:6],
            "line": digits[6:9],
            "conn_qty": int(digits[9:12]),
            "batch": digits[12:14],
        }
    return None


def schedule_column_for_area(area):
    area_prefix = str(area or "").strip().lower().split("(", 1)[0].strip()
    return AREA_COLUMN_MAP.get(area_prefix)


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


def add_schedule_quantity(job_number, schedule_column, quantity):
    token = get_graph_token()
    drive_id, item_id = get_schedule_target(token)
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }

    header_values = get_schedule_range(
        token,
        drive_id,
        item_id,
        f"{SCHEDULE_HEADER_ROW}:{SCHEDULE_HEADER_ROW}",
    )
    header_map = {
        str(value).strip(): index + 1
        for index, value in enumerate(header_values[0])
        if value not in (None, "")
    }
    job_column = header_map.get(SCHEDULE_JOB_HEADER)
    target_column = header_map.get(schedule_column)
    if not job_column or not target_column:
        return {"status": "skipped", "reason": "schedule_column_missing"}

    job_values = get_schedule_range(
        token,
        drive_id,
        item_id,
        f"{column_name(job_column)}{SCHEDULE_FIRST_DATA_ROW}:"
        f"{column_name(job_column)}{SCHEDULE_LAST_DATA_ROW}",
    )
    target_row = None
    for offset, row in enumerate(job_values):
        value = row[0] if row else ""
        if normalize_job(value) == job_number:
            target_row = SCHEDULE_FIRST_DATA_ROW + offset
            break
    if target_row is None:
        return {"status": "skipped", "reason": "job_not_found", "job": job_number}

    cell_address = f"{column_name(target_column)}{target_row}"
    existing_values = get_schedule_range(token, drive_id, item_id, cell_address)
    existing = existing_values[0][0] if existing_values and existing_values[0] else None
    if existing in (None, ""):
        new_value = quantity
    elif isinstance(existing, (int, float)):
        new_value = existing + quantity
    else:
        try:
            new_value = float(str(existing).strip()) + quantity
        except ValueError:
            return {
                "status": "skipped",
                "reason": "target_not_numeric",
                "job": job_number,
                "cell": cell_address,
            }

    url = (
        f"{GRAPH_ROOT}/drives/{drive_id}/items/{item_id}/workbook"
        f"/worksheets/{quote_graph_path(SCHEDULE_SHEET_NAME)}"
        f"/range(address='{cell_address}')"
    )
    response = requests.patch(url, headers=headers, json={"values": [[new_value]]}, timeout=30)
    response.raise_for_status()
    return {
        "status": "updated",
        "job": job_number,
        "column": schedule_column,
        "cell": cell_address,
        "added": quantity,
        "new_value": new_value,
    }


def get_schedule_range(token, drive_id, item_id, address):
    url = (
        f"{GRAPH_ROOT}/drives/{drive_id}/items/{item_id}/workbook"
        f"/worksheets/{quote_graph_path(SCHEDULE_SHEET_NAME)}"
        f"/range(address='{address}')"
    )
    response = requests.get(
        url,
        headers={"Authorization": f"Bearer {token}"},
        timeout=30,
    )
    response.raise_for_status()
    return response.json()["values"]


def get_schedule_target(token):
    drive_id = os.environ.get("SCHEDULE_DRIVE_ID")
    item_id = os.environ.get("SCHEDULE_ITEM_ID")
    if drive_id and item_id:
        return drive_id, item_id

    site_path = required_setting("SCHEDULE_SITE_PATH")
    file_path = required_setting("SCHEDULE_FILE_PATH").strip("/")
    site_response = requests.get(
        f"{GRAPH_ROOT}/sites/{quote(site_path, safe=':/')}",
        headers={"Authorization": f"Bearer {token}"},
        timeout=30,
    )
    site_response.raise_for_status()
    site_id = site_response.json()["id"]

    item_response = requests.get(
        f"{GRAPH_ROOT}/sites/{site_id}/drive/root:/{quote(file_path, safe='/')}",
        headers={"Authorization": f"Bearer {token}"},
        timeout=30,
    )
    item_response.raise_for_status()
    item = item_response.json()
    return item["parentReference"]["driveId"], item["id"]


def normalize_job(value):
    if value is None:
        return ""
    if isinstance(value, (int, float)):
        return str(int(value)).zfill(6)
    digits = re.sub(r"\D", "", str(value))
    return digits.zfill(6) if digits else ""


def column_name(index):
    name = ""
    while index:
        index, remainder = divmod(index - 1, 26)
        name = chr(65 + remainder) + name
    return name


def quote_graph_path(value):
    return quote(str(value), safe="")


def append_excel_row(values):
    token = get_graph_token()
    try:
        drive_id, item_id = get_excel_target(token)
    except requests.HTTPError as error:
        raise RuntimeError(
            f"Could not resolve the canonical workbook: {graph_error_detail(error)}"
        ) from error

    url = (
        f"{GRAPH_ROOT}/drives/{drive_id}/items/{item_id}"
        f"/workbook/tables/{TABLE_NAME}/rows/add"
    )
    try:
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
    except requests.HTTPError as error:
        raise RuntimeError(
            f"Could not append to the {TABLE_NAME} table: {graph_error_detail(error)}"
        ) from error


def get_excel_target(token):
    user_principal = os.environ.get("EXCEL_USER_PRINCIPAL", "").strip()
    file_path = os.environ.get("EXCEL_FILE_PATH", "").strip("/")
    if user_principal and file_path:
        item_response = requests.get(
            f"{GRAPH_ROOT}/users/{quote(user_principal, safe='@')}/drive/root:"
            f"/{quote(file_path, safe='/')}",
            headers={"Authorization": f"Bearer {token}"},
            timeout=30,
        )
        item_response.raise_for_status()
        item = item_response.json()
        return item["parentReference"]["driveId"], item["id"]

    site_path = os.environ.get("EXCEL_SITE_PATH", "").strip()
    if site_path and file_path:
        site_response = requests.get(
            f"{GRAPH_ROOT}/sites/{quote(site_path, safe=':/')}",
            headers={"Authorization": f"Bearer {token}"},
            timeout=30,
        )
        site_response.raise_for_status()
        site_id = site_response.json()["id"]

        item_response = requests.get(
            f"{GRAPH_ROOT}/sites/{site_id}/drive/root:/{quote(file_path, safe='/')}",
            headers={"Authorization": f"Bearer {token}"},
            timeout=30,
        )
        item_response.raise_for_status()
        item = item_response.json()
        return item["parentReference"]["driveId"], item["id"]

    workbook_url = os.environ.get("EXCEL_FILE_URL", "").strip()
    if workbook_url:
        encoded_url = base64.urlsafe_b64encode(workbook_url.encode("utf-8")).decode("ascii")
        share_id = f"u!{encoded_url.rstrip('=')}"
        response = requests.get(
            f"{GRAPH_ROOT}/shares/{share_id}/driveItem",
            headers={"Authorization": f"Bearer {token}"},
            timeout=30,
        )
        response.raise_for_status()
        item = response.json()
        return item["parentReference"]["driveId"], item["id"]

    return required_setting("EXCEL_DRIVE_ID"), required_setting("EXCEL_ITEM_ID")


def graph_error_detail(error):
    if error.response is not None:
        return error.response.text
    return str(error)


def get_graph_token():
    if os.environ.get("AZURE_USE_MANAGED_IDENTITY", "").lower() == "true":
        endpoint = required_setting("IDENTITY_ENDPOINT")
        identity_header = required_setting("IDENTITY_HEADER")
        response = requests.get(
            endpoint,
            params={
                "resource": "https://graph.microsoft.com",
                "api-version": "2019-08-01",
            },
            headers={"X-IDENTITY-HEADER": identity_header},
            timeout=30,
        )
        response.raise_for_status()
        return response.json()["access_token"]

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
