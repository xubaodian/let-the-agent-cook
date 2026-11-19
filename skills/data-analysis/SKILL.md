---
name: data-analysis
description: Clean and reconcile tabular inputs, derive metrics with a repeatable script, and publish evidence for reports or dashboards. Use for CSV, JSON, spreadsheet, or MCP data with several calculation steps.
---

# Data analysis

1. Identify the grain, date range, filters, units, and key of each source. Keep source files and downloaded pages intact. Follow each pagination cursor until the source reports completion.
2. Write a script that normalizes types and reports invalid rows, duplicate keys, missing values, and join coverage. Resolve conflicting records against the source or record them separately. Track which rows contributed to each metric.
3. Compute each metric once. Save a small `metrics.json` with the period, units, source references, values, and denominators where applicable. Keep exact numeric values in this file and apply display rounding in consumers.
4. Test the calculation against a known row or a small hand-checkable group. Check that group counts reconcile to the total. Compare benchmarks at the same entity level, window, and unit.
5. Pass the same metrics file to charts, workbooks, slides, and pages. Record unresolved gaps in the deliverable. Use null for unavailable metrics and preserve zero as an observed value.

When the reporting plugin is enabled, inspect `assets/reporting/build_review.py` for a worked example that deduplicates order IDs and keeps reported amounts separate from orders with an unknown amount.
