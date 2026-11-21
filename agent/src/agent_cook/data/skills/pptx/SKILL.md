---
name: pptx
description: Create, revise, and verify editable PowerPoint decks. Use for presentations, proposals, project updates, business reviews, or requests for PPT slides with optional cover art and illustrations.
---

# PowerPoint

1. Set the audience, objective, language, and slide count. Plan titles as a narrative and give each slide one point. Apply the requested template or visual style consistently.
2. Decide where imagery will help the audience understand the story. A promotional deck may benefit from an original cover or a few illustrations. Reuse matching assets and read the `image-assets` Skill at its catalog entry when planning new imagery. If `generate_image` is available, request a crop that fits the layout and use the returned image path in the deck.
3. Build the editable deck with `python-pptx`. Keep titles, labels, tables, and charts editable. Use verified figures from the shared metrics for data slides and label the period, units, and benchmark cohort.
4. Match the aspect ratio when placing photos. Give text enough space and contrast. Review the rendered slides when a renderer is available. Reopen the deck to verify slide count, order, text, images, and numeric claims.
5. Submit the `.pptx` and any source files the user needs to edit or reproduce it. If the project studio is enabled, its `assets/reporting/build_review.py` demonstrates building a deck from the same metrics as a workbook and brief.
