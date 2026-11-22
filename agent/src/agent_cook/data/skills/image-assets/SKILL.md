---
name: image-assets
description: Plan and produce imagery for PowerPoint covers, website heroes, campaign visuals, and illustrations. Use to choose where image generation adds value and to prepare assets that fit the final layout.
---

# Image assets

## Language and storyline

Write captions, alt text, and any visible labels in the requested language. Keep the surrounding page or deck's language by default, and match the user's language for standalone work. Use concrete descriptions and familiar words. Define what the viewer should understand at each point in the set. Make the images support the surrounding story and give each image a clear role.

## Produce and verify

1. Identify the image's role, dimensions, crop, and placement before producing it. Start with suitable user-supplied or workspace images. Generate a small coordinated set when original imagery adds value to the brief.
2. When `generate_image` is available, describe the subject, composition, style, palette, and placement. Set the aspect ratio to the intended slot and specify negative space for any overlay. For a slide cover or site hero with editable copy, request a text-free visual.
3. Use the workspace file returned by the tool. Crop or optimize a copy with Pillow when useful. Keep the visual consistent with the deck or site and verify its readability at display size.
4. Leave exact numbers, charts, labels, and official marks to editable authoring tools and provided assets. Treat generated scenes as illustrations. When generation is unavailable, work with supplied assets, typography, or editable shapes.
5. Embed deck illustrations in the `.pptx`. For a site, use local assets with relative URLs and suitable alt text. Include referenced assets in the delivered bundle and check them in the final page or slide.
