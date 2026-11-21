import os
from collections.abc import Mapping, Sequence
from contextlib import AsyncExitStack
from dataclasses import replace
from functools import partial
from pathlib import Path

from agent_cook.core.run import run_agent
from agent_cook.core.types import AgentSpec, RunResult
from agent_cook.events.journal import RunEvents
from agent_cook.extensions.hooks import HookRunner
from agent_cook.extensions.mcp import register_mcp_tools
from agent_cook.extensions.plugins import Plugin
from agent_cook.extensions.skills import SkillCatalog
from agent_cook.model.catalog import MODEL_APIS
from agent_cook.model.client import ModelTurn
from agent_cook.model.openrouter import OPENROUTER_BASE_URL, OpenRouterModel
from agent_cook.model.openrouter_images import OpenRouterImageGenerator
from agent_cook.model.openrouter_responses import OpenRouterResponsesModel
from agent_cook.protocol.request import WebOptions
from agent_cook.retry import RETRY_DELAYS
from agent_cook.sandbox.interface import SandboxFactory
from agent_cook.state.session import Message, Session
from agent_cook.tools.artifacts import ArtifactExporter
from agent_cook.tools.images import register_image_tool
from agent_cook.tools.public_browser import PublicBrowser
from agent_cook.tools.registry import ToolRegistry
from agent_cook.tools.spec import ToolDefinition
from agent_cook.tools.web import register_exa_tools
from agent_cook.tools.workspace import workspace_tools


class _OpenRouterModels:
    def __init__(self, client, override: str | None, apis: Mapping[str, str] | None) -> None:
        self._chat = OpenRouterModel(client)
        self._responses = OpenRouterResponsesModel(client)
        self._override = override
        self._apis = apis or {}

    async def next_turn(
        self, *, model: str, messages: Sequence[Message], tools: Sequence[ToolDefinition], web_enabled: bool = False,
    ) -> ModelTurn:
        api = self._override or self._apis.get(model, "chat_completions")
        adapter = self._responses if api == "responses" else self._chat
        return await adapter.next_turn(model=model, messages=messages, tools=tools, web_enabled=web_enabled)


async def run_task(
    spec: AgentSpec,
    task: str,
    api_key: str,
    factory: SandboxFactory | None,
    inputs: dict[str, bytes],
    *,
    events: RunEvents,
    artifact_dir: Path,
    model_api: str | None = None,
    model_apis: Mapping[str, str] | None = None,
    image_model: str | None = None,
    skills: SkillCatalog | None = None,
    plugins: tuple[Plugin, ...] = (),
    mcp_tools: tuple[str, ...] | None = None,
    web: WebOptions = WebOptions(),
    exa_api_key: str | None = None,
    browser_factory: SandboxFactory | None = None,
    session: Session | None = None,
) -> RunResult:
    """Assemble and execute one task for either the CLI or HTTP service."""
    if model_api is not None and model_api not in MODEL_APIS:
        raise ValueError("model_api must be chat_completions or responses.")

    from httpx import Timeout
    from openai import AsyncOpenAI

    # A stalled model request should time out before the run deadline.
    request_timeout = min(600.0, spec.timeout_seconds * 0.75)
    async with AsyncExitStack() as resources:
        client = await resources.enter_async_context(AsyncOpenAI(
            api_key=api_key,
            base_url=OPENROUTER_BASE_URL,
            max_retries=len(RETRY_DELAYS),
            timeout=Timeout(request_timeout, connect=min(request_timeout, 5.0)),
        ))
        registry = ToolRegistry()
        tool_names: tuple[str, ...] = ()
        if factory is not None:
            exporter = ArtifactExporter(artifact_dir)
            browser = (
                PublicBrowser(browser_factory, resources, events.run_id, web.browser_origins)
                if browser_factory is not None else None
            )
            registry, tool_names = workspace_tools(exporter, public_browser=browser)
            if image_model is not None:
                image_tool = register_image_tool(registry, OpenRouterImageGenerator(client, image_model))
                tool_names = (*tool_names, image_tool)
        servers = tuple(server for plugin in plugins for server in plugin.mcp_servers)
        mcp_names = await register_mcp_tools(resources, registry, servers, selected=mcp_tools)
        exa_key = (exa_api_key or os.environ.get("EXA_API_KEY", "")).strip()
        exa_names = register_exa_tools(registry, exa_key) if exa_key and web.enabled is not False else ()
        mcp_names += exa_names
        tool_names += mcp_names
        hosted_web = web.enabled is True and not exa_names
        instructions = spec.instructions
        if hosted_web or exa_names:
            instructions += "\n\nUse web search to find sources and web fetch to read a known URL. Cite the sources you use."
        if browser_factory is not None:
            instructions += (
                "\n\nUse Playwright on an approved public origin when reading the page requires interaction or a screenshot."
            )
        spec = replace(
            spec, tool_names=tool_names, web_enabled=hosted_web,
            instructions=skills.instructions(instructions) if skills is not None else instructions.strip(),
        )
        hooks = tuple(hook for plugin in plugins for hook in plugin.hooks)
        trees = tuple(tree for plugin in plugins for tree in plugin.files)
        workspace_inputs = (
            partial(skills.workspace_files, inputs, extra_trees=trees)
            if skills is not None and (skills.names or trees) else inputs
        )
        if plugins or skills is not None or mcp_tools is not None or exa_names:
            events.emit(
                "extensions.loaded", plugins=[plugin.name for plugin in plugins],
                skills=list(skills.names) if skills is not None else [],
                mcp_tools=list(mcp_names),
            )
        return await run_agent(
            spec,
            task,
            model=_OpenRouterModels(client, model_api, model_apis),
            tools=registry,
            sandbox_factory=factory,
            input_files=workspace_inputs,
            events=events,
            subagents=tuple(agent for plugin in plugins for agent in plugin.subagents),
            hook=HookRunner(hooks) if hooks else None,
            session=session,
        )
