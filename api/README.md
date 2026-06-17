# Scanner Work Tracker API

Azure Function endpoint for appending scan entries to a live Excel table through Microsoft Graph.
When Microsoft Graph settings are not configured, entries are stored in Azure Table Storage and exposed as a CSV feed for Excel.

## Endpoint

`POST /api/save-entry`

```json
{
  "position_id": "FM2000081",
  "payroll_name": "Chen, Jiajun",
  "batch_id": "12345",
  "tablet_id": "Tablet-01"
}
```

## Required App Settings

### Azure Table Storage Mode

- `AzureWebJobsStorage`
- `STORAGE_TABLE_NAME`

### Microsoft Graph Excel Mode

- `AZURE_TENANT_ID`
- `AZURE_CLIENT_ID`
- `AZURE_CLIENT_SECRET`
- `EXCEL_DRIVE_ID`
- `EXCEL_ITEM_ID`
- `EXCEL_TABLE_NAME`

The Excel workbook must be stored in OneDrive for Business or SharePoint and contain a table named `WorkTracker`.

Recommended columns:

- Created At
- Position ID
- Payroll Name
- Batch ID
- Tablet ID

### Optional Production Schedule Updates

When enabled, each saved scan can also update the live production schedule workbook.
The function parses the barcode as:

- 14 digits: `6 job + 3 line + 3 connector quantity + 2 batch`
- 13 digits: treated as missing one line digit, `6 job + 2 line + 3 connector quantity + 2 batch`

The 6-digit job number is matched against the schedule's `Job #` column. The operator area prefix maps to the matching schedule column:

- `prep(...)` -> `Prep`
- `term(...)` -> `Terminated`
- `polish(...)` -> `Polish`
- `scope(...)` -> `Scope`
- `test(...)` -> `Test`
- `cut(...)` -> `Cut`
- `pack(...)` -> `In Pack-Ship`

Set these app settings to turn this on:

- `SCHEDULE_ENABLED=true`
- Either `SCHEDULE_DRIVE_ID` and `SCHEDULE_ITEM_ID`
- Or `SCHEDULE_SITE_PATH=nsiindustries.sharepoint.com:/sites/LynnBTProduction` and `SCHEDULE_FILE_PATH=Shared Documents/General/New Fiber Schedule.xlsx`
- `SCHEDULE_SHEET_NAME=PA Fiber`
- `SCHEDULE_HEADER_ROW=3`
- `SCHEDULE_FIRST_DATA_ROW=4`
- `SCHEDULE_LAST_DATA_ROW=358`
- `SCHEDULE_JOB_HEADER=Job #`

Blank target cells are treated as zero. Numeric target cells have the connector quantity added. Text/status cells, missing jobs, unmapped areas, and invalid barcode lengths are skipped and logged so scanner saves still succeed.

The installed tablet app does not send operator area in its cloud payload, so the function resolves area by `Position ID` using bundled `operator_areas.json`. To override without redeploying code, set `OPERATOR_AREA_MAP_JSON` to a JSON object like `{"FM2000118":"prep(reg)"}`.
