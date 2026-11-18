import json
import re
from collections.abc import Sequence
from copy import deepcopy
from dataclasses import dataclass
from typing import NoReturn

from jsonschema import Draft202012Validator, ValidationError

from agent_cook.tools.spec import ToolCall, ToolContext, ToolDefinition, ToolHandler, ToolResult


@dataclass(frozen=True, slots=True)
class _Binding:
    definition: ToolDefinition
    handler: ToolHandler
    validator: Draft202012Validator


class ToolRegistry:
    def __init__(self) -> None:
        self._bindings: dict[str, _Binding] = {}

    def register(self, definition: ToolDefinition, handler: ToolHandler) -> None:
        if re.fullmatch(r"[A-Za-z0-9_-]{1,64}", definition.name) is None:
            raise ValueError("A tool name must be 1 to 64 letters, digits, underscores, or hyphens.")
        if definition.name in self._bindings:
            raise ValueError(f"Tool '{definition.name}' is already registered.")
        schema = deepcopy(definition.input_schema)
        if schema.get("type") != "object":
            raise ValueError("A tool input schema must describe a JSON object.")
        Draft202012Validator.check_schema(schema)
        spec = ToolDefinition(definition.name, definition.description, schema)
        self._bindings[spec.name] = _Binding(spec, handler, Draft202012Validator(schema))

    def definitions(self, names: Sequence[str]) -> tuple[ToolDefinition, ...]:
        definitions = []
        for name in names:
            binding = self._bindings.get(name)
            if binding is None:
                raise ValueError(f"Tool '{name}' is enabled but has no registered handler.")
            definitions.append(deepcopy(binding.definition))
        return tuple(definitions)

    async def invoke(self, call: ToolCall, context: ToolContext) -> ToolResult:
        binding = self._bindings.get(call.name)
        if binding is None or call.name not in context.allowed_tools:
            return ToolResult(f"Tool '{call.name}' is unavailable in this run.", is_error=True)

        try:
            arguments = json.loads(call.arguments, parse_constant=_invalid_json_constant)
        except ValueError:
            return ToolResult("Tool arguments must be valid JSON.", is_error=True)

        try:
            binding.validator.validate(arguments)
        except ValidationError as error:
            location = error.json_path
            return ToolResult(f"Tool arguments at {location} failed schema validation.", is_error=True)

        try:
            result = await binding.handler(arguments, context)
        except Exception as error:
            return ToolResult(f"Tool '{call.name}' failed ({type(error).__name__}).", is_error=True)
        if not isinstance(result, ToolResult):
            return ToolResult(f"Tool '{call.name}' returned an invalid result.", is_error=True)
        return result


def _invalid_json_constant(value: str) -> NoReturn:
    raise ValueError(f"Invalid JSON constant {value}")
