# Scanner Work Tracker API

Azure Function endpoint for appending scan entries to a live Excel table through Microsoft Graph.

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
