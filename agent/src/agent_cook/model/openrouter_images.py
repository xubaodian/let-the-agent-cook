import base64
from dataclasses import dataclass
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from functools import partial
from math import isfinite
from typing import TYPE_CHECKING

from agent_cook.model.client import ModelCallTimeout, model_call_deadline
from agent_cook.retry import retry_transient
from agent_cook.sandbox.limits import MAX_FILE_BYTES

if TYPE_CHECKING:
    from openai import APIStatusError, AsyncOpenAI


class ImageGenerationError(RuntimeError):
    """OpenRouter could not return a usable image."""


@dataclass(frozen=True, slots=True)
class GeneratedImage:
    content: bytes
    media_type: str
    extension: str


class OpenRouterImageGenerator:
    def __init__(self, client: "AsyncOpenAI", model: str) -> None:
        # A retry can bill for another generation if the first response was lost.
        self._client = client.with_options(max_retries=0)
        self.model = model

    async def generate(
        self, prompt: str, *, aspect_ratio: str | None = None, resolution: str | None = None
    ) -> GeneratedImage:
        from openai import APIConnectionError, APIStatusError, APITimeoutError

        body = {"model": self.model, "prompt": prompt}
        if aspect_ratio is not None:
            body["aspect_ratio"] = aspect_ratio
        if resolution is not None:
            body["resolution"] = resolution

        try:
            async with model_call_deadline():
                response = await retry_transient(
                    partial(self._client.post, "/images", cast_to=object, body=body),
                    lambda error: isinstance(error, APIStatusError) and error.status_code == 429,
                    retry_after=_retry_after,
                )
        except (APITimeoutError, ModelCallTimeout) as error:
            raise ImageGenerationError("OpenRouter image generation timed out.") from error
        except APIConnectionError as error:
            raise ImageGenerationError("Could not reach OpenRouter for image generation.") from error
        except APIStatusError as error:
            raise ImageGenerationError(
                f"OpenRouter image generation failed (HTTP {error.status_code}). "
                "Check the configured image model and its supported options."
            ) from error
        return _decode_image(response)


def _retry_after(error: "APIStatusError") -> float:
    value = error.response.headers.get("Retry-After")
    if value is None:
        return 0
    try:
        seconds = float(value)
    except ValueError:
        try:
            seconds = (parsedate_to_datetime(value) - datetime.now(UTC)).total_seconds()
        except (TypeError, ValueError, OverflowError):
            return 0
    return seconds if isfinite(seconds) else 0


def _decode_image(response: object) -> GeneratedImage:
    images = response.get("data") if isinstance(response, dict) else None
    if not isinstance(images, list) or len(images) != 1 or not isinstance(images[0], dict):
        raise ImageGenerationError("OpenRouter did not return a single image.")
    encoded = images[0].get("b64_json")
    if not isinstance(encoded, str) or not encoded:
        raise ImageGenerationError("OpenRouter did not return image data.")

    size_error = "The generated image exceeds the workspace file limit. Try a lower resolution."
    if len(encoded) > 4 * ((MAX_FILE_BYTES + 2) // 3):
        raise ImageGenerationError(size_error)
    try:
        content = base64.b64decode(encoded, validate=True)
    except ValueError as error:
        raise ImageGenerationError("OpenRouter returned invalid base64 image data.") from error
    if len(content) > MAX_FILE_BYTES:
        raise ImageGenerationError(size_error)

    if content.startswith(b"\x89PNG\r\n\x1a\n"):
        return GeneratedImage(content, "image/png", ".png")
    if content.startswith(b"\xff\xd8\xff"):
        return GeneratedImage(content, "image/jpeg", ".jpg")
    if content.startswith(b"RIFF") and content[8:12] == b"WEBP":
        return GeneratedImage(content, "image/webp", ".webp")
    raise ImageGenerationError("OpenRouter returned an unsupported image format. Expected PNG, JPEG, or WebP.")
