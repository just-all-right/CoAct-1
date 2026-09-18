from __future__ import annotations

import sys
from types import TracebackType
from typing import Any, Optional, Union

import diskcache

from .abstract_cache_base import AbstractCache

if sys.version_info >= (3, 11):
    from typing import Self
else:
    from typing_extensions import Self


class DiskCache(AbstractCache):
    def __init__(self, seed: Union[str, int]):
        self.cache = diskcache.Cache(seed)

    def get(self, key: str, default: Optional[Any] = None) -> Optional[Any]:
        return self.cache.get(key, default)

    def set(self, key: str, value: Any) -> None:
        self.cache.set(key, value)

    def close(self) -> None:
        self.cache.close()

    def __enter__(self) -> Self:
        return self

    def __exit__(
        self,
        exc_type: Optional[type[BaseException]],
        exc_value: Optional[BaseException],
        traceback: Optional[TracebackType],
    ) -> None:
        self.close()