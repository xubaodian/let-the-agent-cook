---
name: spreadsheet
description: Build and check editable Excel workbooks from source rows and computed metrics. Use for reconciliations, analysis tables, formula summaries, and audited data handoffs.
---

# Spreadsheet

1. Organize raw inputs, calculated summaries, and validation notes in clearly named sheets. Preserve numeric cell types and write missing values as blank cells. Use explicit number formats for currencies and percentages.
2. Build the workbook with Python and `openpyxl`. Use formulas where a reviewer should be able to change inputs. Document formula ranges and include verified summary values when an automated reader also needs the numbers.
3. Add readable headers, widths, and filters where useful. Record the period, source, and units near the summary. Keep row IDs that assist reconciliation separate from audience-facing sheets.
4. Reopen the output to check sheet names, cell types, formulas, and totals against the shared metrics. Treat a value read from a recalculated workbook as separate evidence from inspecting formula text.

Use the workbook code in `assets/reporting/build_review.py` as an example when that asset is available.
