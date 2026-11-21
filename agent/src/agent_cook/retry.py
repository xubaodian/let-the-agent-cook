import asyncio
from collections.abc import Awaitable, Callable
from typing import TypeVar

RETRY_DELAYS = (0.25, 0.5)
_T = TypeVar("_T")


async def retry_transient(
    action: Callable[[], Awaitable[_T]],
    retry_if: Callable[[Exception], bool],
    *,
    retry_after: Callable[[Exception], float] | None = None,
) -> _T:
    for delay in RETRY_DELAYS:
        try:
            return await action()
        except Exception as error:
            if not retry_if(error):
                raise
            if retry_after is not None:
                delay = max(delay, retry_after(error))
        await asyncio.sleep(delay)
    return await action()
