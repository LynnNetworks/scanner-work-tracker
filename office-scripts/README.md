# Office Scripts

## Daily area output

Paste `daily-area-output.ts` into a new Office Script in `Scanner_Work_Tracker.xlsx`, then name it
`Build Daily Area Output`.

The script creates or replaces the `Daily Output` worksheet's generated report tables. It sums the
quantity in batch-ID characters 10-12 for each operator within each exact tracker area, for the
requested `report_date` (`YYYY-MM-DD`). When no date is supplied, it uses the report date currently
shown in `Daily Output!B2`; if that cell is blank, it uses the current UTC date.

The script expects `WorkTracker` to have `Created At`, `Payroll Name`, `Area`, and `Batch ID`
columns. It excludes blank, malformed, and zero-quantity scan records and lists them at the end of
the report rather than including them in totals.

## Scheduled refresh flow

Create a scheduled Power Automate cloud flow with a **Recurrence** trigger. Add **Excel Online
(Business) > Run script**, select `Scanner_Work_Tracker.xlsx`, select `Build Daily Area Output`, and
pass the date as an ISO date string (`YYYY-MM-DD`). A daily schedule refreshes the prior day's
report; a more frequent schedule keeps the current day's report current during production.

The report is intentionally generated with native Excel tables, not QI Macros. Office Scripts and
Power Automate can run native workbook automation in cloud Excel sessions, while QI Macros is a
desktop Excel add-in and cannot be invoked by a cloud flow.
