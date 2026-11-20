---
name: pptx
description: Build editable PowerPoint decks from verified content and data. Use for business reviews, executive readouts, proposals, or updates that need slides and reproducible source metrics.
---

# PowerPoint

1. Outline the narrative in titles first. Give each slide one point and use the user's template, audience, and locale where specified.
2. Use `python-pptx` to create editable text, tables, and charts. Read numeric values from the shared metrics file. Label the period, units, and benchmark cohort alongside each comparison.
3. Use readable sizes, restrained color, and adequate margins. Confirm that displayed text fits its box at presentation size. Inspect a rendered preview when a renderer is available.
4. Reopen the saved deck. Verify slide order, titles, text, and numeric claims against the sources. Record any remaining visual checks before submission.

The sample export in `assets/reporting/build_review.py` shows how to populate a deck from the same metrics used by a workbook and a brief.
