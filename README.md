# Scanner Work Tracker

Simple Android-friendly starter app for recording scanned or typed Position IDs and batch IDs.

The app records scans in the shared tracker workbook and validates Position IDs against its
`Area Assignments` sheet. No `operators.csv` or `Fiber Roster.xlsx` is used by the tablet.

## What It Does

- Accepts a Position ID.
- Validates the Position ID and payroll name against the shared tracker spreadsheet.
- Connects the Position ID to the payroll name.
- Accepts a numeric batch ID.
- Appends each saved record to an Excel workbook.
- Shows the 10 most recent records with name, Position ID, batch, and timestamp.

## Run On Desktop For Testing

Install Kivy, then run:

```powershell
python -m pip install kivy
python main.py
```

## Build For Android

The common Kivy Android build path uses Buildozer, which runs best on Linux or Windows Subsystem for Linux.

From this folder in WSL/Linux:

```bash
python3 -m pip install buildozer
buildozer android debug
```

The generated APK will be under `bin/`.

## Live Excel Through Azure

The app saves only to the lead-managed `Scanner_Work_Tracker.xlsx` workbook through the Azure
Function in `api/`. It never creates or writes a second workbook on a tablet.

High-level flow:

```text
Tablet app -> Azure Function -> Microsoft Graph -> Excel table in OneDrive/SharePoint
```

### Azure Setup Checklist

1. Use the existing lead-managed `Scanner_Work_Tracker.xlsx` workbook as the canonical workbook.
2. Create an Excel table in that workbook named `WorkTracker`.
3. Give the `WorkTracker` table these columns:
   - Created At
   - Position ID
   - Payroll Name
   - Area
   - Batch ID
   - Tablet ID
4. Add a worksheet named `Area Assignments` with an Excel table containing:
   - `position_id`
   - `payroll_name`
   - `area`
   Keep Position ID and Payroll Name locked; give floor leads edit access only to the Area
   column and use Excel data validation for the allowed values.
5. Create an Entra ID app registration for Microsoft Graph access.
6. Create a client secret for that app registration.
7. Grant the app permission to write to the workbook.
8. Create an Azure Function App using Python 3.11.
9. Add these Function App settings:
   - `AZURE_TENANT_ID`
   - `AZURE_CLIENT_ID`
   - `AZURE_CLIENT_SECRET`
   - `EXCEL_SITE_PATH` as `nsiindustries-my.sharepoint.com:/personal/egenova_thinklynn_com`
   - `EXCEL_FILE_PATH` as `Scanner Work Tracker/Scanner_Work_Tracker.xlsx`
   - `EXCEL_TABLE_NAME`
   - `READ_ACCESS_TOKEN` (a long random secret, shared only with the app build and Power Automate)
10. Deploy `api/` to the Function App.
11. Configure the area-assignment sync described below.
12. Put the Function endpoint URL, key, and `READ_ACCESS_TOKEN` into `app_config.py`.

`app_config.py`:

```python
CLOUD_ENDPOINT_URL = "https://<function-app>.azurewebsites.net/api/save-entry"
CLOUD_FUNCTION_KEY = "<function-key>"
TABLET_ID = "Tablet-01"
READ_ACCESS_TOKEN = "<long-random-secret>"
```

`EXCEL_SITE_PATH` and `EXCEL_FILE_PATH` take priority over `EXCEL_FILE_URL` and the legacy
`EXCEL_DRIVE_ID` and `EXCEL_ITEM_ID` settings. This addresses the exact canonical workbook without
depending on a browser sharing URL. Cloud access is required; the tablet does not carry a roster or
write any workbook locally.

### Shared Area Assignment Sync

Create a Power Automate cloud flow with the **When a row is added, modified or deleted** Excel
trigger for the `Area Assignments` table. Add **List rows present in a table**, then an HTTP
`POST` to `https://<function-app>.azurewebsites.net/api/operator-areas-sync?token=<READ_ACCESS_TOKEN>&code=<function-key>`.
Send the complete table as the `assignments` array. The sync validates the area values and replaces
the live directory atomically, so every tablet sees the updated assignment on its next scan.

### No-App-Registration Fallback

If App registrations are blocked by the Microsoft tenant, the Azure Function stores entries in Azure Table Storage and exposes a CSV endpoint:

```text
https://<function-app>.azurewebsites.net/api/entries.csv
```

Excel can connect to that URL with **Data > From Web** and refresh the table when needed.
