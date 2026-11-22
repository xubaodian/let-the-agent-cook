---
name: data-analysis
description: Clean, reconcile, and analyze CSV, JSON, spreadsheets, or tool data. Use to calculate verified metrics, build editable Excel workbooks, or prepare a shared dataset for reports and dashboards.
---

# Data analysis

1. Identify the grain, keys, date range, filters, and units of each source. Preserve the original inputs and follow pagination when a source supplies cursors.
2. Use a repeatable script to normalize types and count invalid rows, duplicate keys, missing values, and unmatched joins. Use pandas for tabular data, xlrd for legacy .xls, PyArrow for Parquet, and NumPy or SciPy for numerical work. Resolve conflicting records against the source or record the uncertainty.
3. Calculate each metric once. Save verified values, units, denominators, periods, and source references together. Use null for unavailable metrics and preserve zero as an observed value. Reuse the same data in workbooks, slides, briefs, and sites.
4. Check the calculations against hand-checkable rows and reconcile group counts with the total. Match benchmark entity level, period, and unit. Apply display rounding and locale formatting in the delivered report.
5. Use openpyxl to read or edit XLSX and XlsxWriter when creating a formatted workbook. Separate source, summary, and checks into clear sheets. Keep numbers in numeric cells, use formulas where the reviewer needs to change inputs, and reopen the workbook to verify types, ranges, and totals.

When present, use `assets/reporting/build_review.py` as an example of deduplicating order IDs and distinguishing known amounts from missing amounts.
