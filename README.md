# Scanner Work Tracker

Simple Android-friendly starter app for recording scanned or typed Position IDs and batch IDs.

The app stores each entry in a local Excel workbook named `work_tracker.xlsx` in the app folder.
It validates Position IDs against `operators.csv`, which was exported from `Fiber Roster.xlsx`.

## What It Does

- Accepts a Position ID.
- Validates the Position ID against the operator roster.
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

## Refresh The Operator Roster

If `Fiber Roster.xlsx` changes, rerun:

```powershell
python import_roster.py
```

That refreshes `operators.csv`, which is bundled with the app.

## Build For Android

The common Kivy Android build path uses Buildozer, which runs best on Linux or Windows Subsystem for Linux.

From this folder in WSL/Linux:

```bash
python3 -m pip install buildozer
buildozer android debug
```

The generated APK will be under `bin/`.

## Excel Columns

The generated `work_tracker.xlsx` has these columns:

- Created At
- Position ID
- Payroll Name
- Batch ID
- Benefits Class
- Reports To Name
- Position Status

## Next Feature Ideas

- Add batch lookup validation.
- Sync entries to a shared network location or cloud folder. ### this feature has been added ###

## Live Excel Through Azure

The app can save to a live Excel workbook through the Azure Function in `api/`.

High-level flow:

```text
Tablet app -> Azure Function -> Microsoft Graph -> Excel table in OneDrive/SharePoint
```

### Azure Setup Checklist

1. Create `work_tracker.xlsx` in OneDrive for Business or SharePoint.
2. Create an Excel table named `WorkTracker`.
3. Give the table these columns:
   - Created At
   - Position ID
   - Payroll Name
   - Batch ID
   - Tablet ID
4. Create an Entra ID app registration for Microsoft Graph access.
5. Create a client secret for that app registration.
6. Grant the app permission to write to the workbook.
7. Create an Azure Function App using Python 3.11.
8. Add these Function App settings:
   - `AZURE_TENANT_ID`
   - `AZURE_CLIENT_ID`
   - `AZURE_CLIENT_SECRET`
   - `EXCEL_DRIVE_ID`
   - `EXCEL_ITEM_ID`
   - `EXCEL_TABLE_NAME`
9. Deploy `api/` to the Function App.
10. Put the Function endpoint URL and key into `app_config.py`.

`app_config.py`:

```python
CLOUD_ENDPOINT_URL = "https://<function-app>.azurewebsites.net/api/save-entry"
CLOUD_FUNCTION_KEY = "<function-key>"
TABLET_ID = "Tablet-01"
```

When `CLOUD_ENDPOINT_URL` is blank, the app uses local Excel only.

### No-App-Registration Fallback

If App registrations are blocked by the Microsoft tenant, the Azure Function stores entries in Azure Table Storage and exposes a CSV endpoint:

```text
https://<function-app>.azurewebsites.net/api/entries.csv
```

Excel can connect to that URL with **Data > From Web** and refresh the table when needed.
