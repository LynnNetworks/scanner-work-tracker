/**
 * Builds a selected day's operator output tables from WorkTracker.
 * Barcode characters 10-12 hold the completed termination quantity.
 */
function main(
  workbook: ExcelScript.Workbook,
  report_date: string = ""
): {
  reportDate: string;
  validScans: number;
  totalTerminations: number;
  areas: number;
  invalidScans: number;
} {
  const tracker = workbook.getTable("WorkTracker");
  if (!tracker) {
    throw new Error("The WorkTracker table was not found.");
  }

  const normalize = (value: unknown): string =>
    String(value ?? "").trim().toLowerCase().replace(/[^a-z0-9]/g, "");
  const display = (value: unknown): string => String(value ?? "").trim();
  const isIsoDate = (value: string): boolean => /^\d{4}-\d{2}-\d{2}$/.test(value);
  const todayUtc = new Date().toISOString().substring(0, 10);

  let output = workbook.getWorksheet("Daily Output");
  if (!output) {
    output = workbook.addWorksheet("Daily Output");
  }

  const selectedDate = display(report_date) || display(output.getRange("B2").getValue()) || todayUtc;
  if (!isIsoDate(selectedDate)) {
    throw new Error("report_date must use YYYY-MM-DD format.");
  }

  for (const table of output.getTables()) {
    if (table.getName().startsWith("DailyOutput_")) {
      table.delete();
    }
  }

  output.getRange("A1:Z1000").clear(ExcelScript.ClearApplyTo.contents);
  output.getRange("A1").setValue("Daily Operator Output");
  output.getRange("A2").setValue("Report Date");
  output.getRange("B2").setValue(selectedDate);
  output.getRange("A4:D4").setValues([[
    "Valid Scans",
    "Total Terminations",
    "Production Areas",
    "Invalid Scans"
  ]]);

  const headers = tracker.getHeaderRowRange().getValues()[0];
  const headerIndexes: { [key: string]: number } = {};
  for (let index = 0; index < headers.length; index++) {
    headerIndexes[normalize(headers[index])] = index;
  }

  const createdAtIndex = headerIndexes["createdat"];
  const payrollNameIndex = headerIndexes["payrollname"];
  const areaIndex = headerIndexes["area"];
  const batchIdIndex = headerIndexes["batchid"];
  if (
    createdAtIndex === undefined ||
    payrollNameIndex === undefined ||
    areaIndex === undefined ||
    batchIdIndex === undefined
  ) {
    throw new Error(
      "WorkTracker must contain Created At, Payroll Name, Area, and Batch ID columns."
    );
  }

  const totals: { [area: string]: { [operator: string]: number } } = {};
  const exceptions: string[][] = [];
  let validScans = 0;
  let totalTerminations = 0;

  for (const row of tracker.getRangeBetweenHeaderAndTotal().getValues()) {
    const createdAt = display(row[createdAtIndex]);
    const area = display(row[areaIndex]);
    const operator = display(row[payrollNameIndex]);
    const batchId = display(row[batchIdIndex]);

    if (!createdAt && !area && !operator && !batchId) {
      continue;
    }
    if (createdAt.substring(0, 10) !== selectedDate) {
      continue;
    }
    if (!area || !operator) {
      exceptions.push([createdAt, operator, area, batchId, "Missing operator or area"]);
      continue;
    }
    if (!/^\d{14}$/.test(batchId)) {
      exceptions.push([createdAt, operator, area, batchId, "Batch ID must contain 14 digits"]);
      continue;
    }

    const quantity = Number(batchId.substring(9, 12));
    if (!Number.isInteger(quantity) || quantity <= 0) {
      exceptions.push([createdAt, operator, area, batchId, "Barcode quantity must be greater than zero"]);
      continue;
    }

    if (!totals[area]) {
      totals[area] = {};
    }
    totals[area][operator] = (totals[area][operator] || 0) + quantity;
    validScans++;
    totalTerminations += quantity;
  }

  const areas = Object.keys(totals).sort((left, right) => left.localeCompare(right));
  output.getRange("A5:D5").setValues([[
    validScans,
    totalTerminations,
    areas.length,
    exceptions.length
  ]]);

  let nextRow = 7;
  for (let areaIndex = 0; areaIndex < areas.length; areaIndex++) {
    const area = areas[areaIndex];
    const operatorTotals = totals[area];
    const operators = Object.keys(operatorTotals).sort((left, right) =>
      left.localeCompare(right)
    );

    output.getCell(nextRow, 0).setValue(area);
    output.getRangeByIndexes(nextRow + 1, 0, 1, 2).setValues([[
      "Operator",
      "Termination Quantity"
    ]]);

    const values = operators.map((operator) => [operator, operatorTotals[operator]]);
    output.getRangeByIndexes(nextRow + 2, 0, values.length, 2).setValues(values);

    const tableRange = output.getRangeByIndexes(nextRow + 1, 0, values.length + 1, 2);
    const table = workbook.addTable(tableRange, true);
    table.setName(`DailyOutput_${areaIndex + 1}`);
    table.setPredefinedTableStyle("TableStyleMedium2");

    nextRow += values.length + 4;
  }

  if (exceptions.length) {
    output.getCell(nextRow, 0).setValue("Excluded Scan Exceptions");
    output.getRangeByIndexes(nextRow + 1, 0, 1, 5).setValues([[
      "Created At",
      "Payroll Name",
      "Area",
      "Batch ID",
      "Reason"
    ]]);
    output.getRangeByIndexes(nextRow + 2, 0, exceptions.length, 5).setValues(exceptions);
  }

  output.getUsedRange().getFormat().autofitColumns();
  output.getUsedRange().getFormat().autofitRows();

  return {
    reportDate: selectedDate,
    validScans,
    totalTerminations,
    areas: areas.length,
    invalidScans: exceptions.length
  };
}
