"""Ownership of fire-and-forget asyncio tasks."""

import asyncio
from collections.abc import Coroutine, Iterator
from typing import Any, TypeVar

T = TypeVar("T")


class BackgroundTasks:
    """Keep running tasks referenced until they finish, and cancel them on exit."""

    def __init__(self) -> None:
        self._tasks: set[asyncio.Task[Any]] = set()

    def spawn(self, coroutine: Coroutine[Any, Any, T]) -> asyncio.Task[T]:
        task = asyncio.create_task(coroutine)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        return task

    def __iter__(self) -> Iterator[asyncio.Task[Any]]:
        return iter(tuple(self._tasks))
