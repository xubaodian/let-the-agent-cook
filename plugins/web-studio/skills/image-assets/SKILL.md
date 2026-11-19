---
name: image-assets
description: Plan, generate, and prepare a coordinated set of images for a site or deck. Use for asset reuse, consistent image treatments, output sizing, and local image references.
---

# Image assets

1. Make a short list of image slots, purpose, aspect ratio, placement, and target size. Reuse suitable workspace imagery first. Reserve charts and other exact graphics for data-driven drawing tools.
2. Use `generate_image` when an image model is enabled and the task calls for new imagery. Set an aspect ratio and a predictable output directory. Keep the response paths for later layout and checks.
3. For crops or optimized formats, write a repeatable transformation using `run_command` and Pillow when available. Keep originals and name the delivery variants consistently.
4. Check the saved dimensions, readability at display size, and relative references in the site or deck. Give meaningful web images concise alt text and use an empty alt value for decorative images.
