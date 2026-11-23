---
name: pptx
description: Create, revise, and verify editable PowerPoint decks. Use for presentations, proposals, project updates, business reviews, or requests for PPT slides with optional cover art and illustrations.
---

# PowerPoint

## Language and storyline

Use the requested language. For revisions, keep the deck's language by default. For a new deck without a language preference, match the user's language. Write for the audience in short sentences and explain essential terms when they first appear. Keep implementation details in speaker notes or an appendix when they support the talk.

Outline the story before building. Open with why the audience should care, use the middle slides to explain the main finding with evidence, and close with what to remember or do. Give each slide one takeaway. Use takeaway titles such as “Repeat orders drove this month's growth.” Read the titles in order before delivering and check that they tell the whole story.

## Build and verify

1. Set the objective and slide count for the audience. Apply the requested template or visual style consistently.
2. Decide where imagery will help the audience understand the story. A promotional deck may benefit from an original cover or a few illustrations. Reuse matching assets and read the `image-assets` Skill at its catalog entry when planning new imagery. If `generate_image` is available, request a crop that fits the layout and use the returned image path in the deck.
3. Build the editable deck with `python-pptx`. Keep titles, labels, tables, and charts editable. Give each chart or table a clear reason to be on the slide and make the result to notice easy to find. Use verified figures from the shared metrics and label the period, units, and benchmark cohort.
4. Match the aspect ratio when placing photos. Give text enough space and contrast. For visual QA, call `run_command` with `["soffice", "--headless", "--convert-to", "pdf:impress_pdf_Export", "--outdir", "preview", "deck.pptx"]` using the actual deck path. Check the PDF page count, rasterize pages with pypdfium2 when needed, and inspect the rendered layout. If rendering fails, report the error and the checks that passed. Reopen the deck to verify slide count, order, text, images, and numeric claims.
5. Submit the `.pptx` and any source files the user needs to edit or reproduce it. If the project studio is enabled, its `assets/reporting/build_review.py` demonstrates building a deck from the same metrics as a workbook and brief.
