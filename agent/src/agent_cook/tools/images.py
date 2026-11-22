import json
from pathlib import PurePosixPath
from typing import Any
from uuid import uuid4

from agent_cook.model.openrouter_images import ImageGenerationError, OpenRouterImageGenerator
from agent_cook.sandbox.paths import workspace_path
from agent_cook.tools.registry import ToolRegistry
from agent_cook.tools.spec import ToolContext, ToolDefinition, ToolError, ToolResult


_GENERATE_IMAGE = ToolDefinition(
    "generate_image",
    "Generate one original image with the configured OpenRouter image model. "
    "Use it when new imagery supports the deliverable, such as a website hero, PowerPoint cover, "
    "campaign concept, or explanatory illustration. Reuse suitable supplied assets first. "
    "Match the aspect ratio to the layout and place the returned workspace file in the final work. "
    "Use editable text, source data, or design code for exact content such as charts and labels.",
    {
        "type": "object",
        "properties": {
            "prompt": {
                "type": "string", "minLength": 1, "maxLength": 8000,
                "description": "Describe the visual's subject, placement, style, composition, and space for overlays.",
            },
            "aspect_ratio": {
                "type": "string",
                "maxLength": 9,
                "pattern": r"^(auto|[1-9][0-9]*:[1-9][0-9]*)$",
                "description": "Optional aspect ratio such as 1:1, 16:9, or 9:16. Defaults to the model's choice.",
            },
            "resolution": {
                "enum": ["512", "768", "1K", "1.5K", "2K", "4K"],
                "description": "Optional resolution tier. Choose a tier supported by the configured image model.",
            },
            "output_dir": {
                "type": "string",
                "minLength": 1,
                "maxLength": 240,
                "description": "Workspace-relative output directory. Defaults to generated-images.",
            },
        },
        "required": ["prompt"],
        "additionalProperties": False,
    },
)


def register_image_tool(registry: ToolRegistry, generator: OpenRouterImageGenerator) -> str:
    async def generate_image(arguments: dict[str, Any], context: ToolContext) -> ToolResult:
        prompt = arguments["prompt"].strip()
        if not prompt:
            raise ToolError("Provide a prompt with at least one non-whitespace character.")

        try:
            directory = workspace_path(arguments.get("output_dir", "generated-images"), allow_root=True)
        except ValueError as error:
            raise ToolError(str(error)) from error

        sandbox = await context.get_sandbox()
        try:
            image = await generator.generate(
                prompt, aspect_ratio=arguments.get("aspect_ratio"), resolution=arguments.get("resolution")
            )
        except ImageGenerationError as error:
            raise ToolError(str(error)) from error

        path = (PurePosixPath(directory) / f"{uuid4().hex}{image.extension}").as_posix()
        await sandbox.put_file(path, image.content)
        return ToolResult(json.dumps({
            "path": path,
            "media_type": image.media_type,
            "bytes": len(image.content),
            "model": generator.model,
        }))

    registry.register(_GENERATE_IMAGE, generate_image)
    return _GENERATE_IMAGE.name
