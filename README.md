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

- Add barcode scanner keyboard-wedge support polish.
- Add a button to export/share the Excel workbook from Android.
- Add batch lookup validation.
- Sync entries to a shared network location or cloud folder.
