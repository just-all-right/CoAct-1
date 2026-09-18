from __future__ import annotations

from contextvars import ContextVar
from types import TracebackType
from typing import Any, Optional, Union

from ..doc_utils import export_module
from .abstract_cache_base import AbstractCache
from .cache_factory import CacheFactory


@export_module("autogen")
class Cache(AbstractCache):
    _current_cache: ContextVar[Cache] = ContextVar("current_cache", default=None)

    ALLOWED_CONFIG_KEYS = [
        "cache_seed",
        "redis_url",
        "cache_path_root",
        "cosmos_db_config",
    ]

    @staticmethod
    def redis(cache_seed: Union[str, int] = 42, redis_url: str = "redis://localhost:6379/0") -> Cache:
        return Cache({"cache_seed": cache_seed, "redis_url": redis_url})

    @staticmethod
    def disk(cache_seed: Union[str, int] = 42, cache_path_root: str = ".cache") -> Cache:
        return Cache({"cache_seed": cache_seed, "cache_path_root": cache_path_root})

    @staticmethod
    def cosmos_db(
        connection_string: Optional[str] = None,
        container_id: Optional[str] = None,
        cache_seed: Union[str, int] = 42,
        client: Optional[Any] = None,
    ) -> Cache:
        cosmos_db_config = {
            "connection_string": connection_string,
            "database_id": "autogen_cache",
            "container_id": container_id,
            "client": client,
        }
        return Cache({"cache_seed": str(cache_seed), "cosmos_db_config": cosmos_db_config})

    def __init__(self, config: dict[str, Any]):
        self.config = config
        self.config["cache_seed"] = str(self.config.get("cache_seed", 42))
        for key in self.config:
            if key not in self.ALLOWED_CONFIG_KEYS:
                raise ValueError(f"Invalid config key: {key}")
        self.cache = CacheFactory.cache_factory(
            seed=self.config["cache_seed"],
            redis_url=self.config.get("redis_url"),
            cache_path_root=self.config.get("cache_path_root"),
            cosmosdb_config=self.config.get("cosmos_db_config"),
        )

    def __enter__(self) -> Cache:
        self._previous_cache = self.__class__._current_cache.get(None)
        self._token = self.__class__._current_cache.set(self)
        self.cache.__enter__()
        return self

    def __exit__(
        self,
        exc_type: Optional[type[BaseException]],
        exc_value: Optional[BaseException],
        traceback: Optional[TracebackType],
    ) -> None:
        result = self.cache.__exit__(exc_type, exc_value, traceback)
        try:
            self.__class__._current_cache.reset(self._token)
        except RuntimeError:
            if self._previous_cache is not None:
                self.__class__._current_cache.set(self._previous_cache)
        return result

    def get(self, key: str, default: Optional[Any] = None) -> Optional[Any]:
        return self.cache.get(key, default)

    def set(self, key: str, value: Any) -> None:
        self.cache.set(key, value)

    def close(self) -> None:
        self.cache.close()

    @classmethod
    def get_current_cache(cls, cache: Optional[Cache] = None) -> Optional[Cache]:
        if cache is not None:
            return cache
        try:
            return cls._current_cache.get()
        except LookupError:
            return None