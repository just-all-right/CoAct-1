import logging
import os
from typing import Any, Optional, Union

from ..import_utils import optional_import_block
from .abstract_cache_base import AbstractCache
from .disk_cache import DiskCache


class CacheFactory:
    @staticmethod
    def cache_factory(
        seed: Union[str, int],
        redis_url: Optional[str] = None,
        cache_path_root: str = ".cache",
        cosmosdb_config: Optional[dict[str, Any]] = None,
    ) -> AbstractCache:
        if redis_url:
            with optional_import_block() as result:
                from .redis_cache import RedisCache

            if result.is_successful:
                return RedisCache(seed, redis_url)
            logging.warning("RedisCache is not available. Falling back to DiskCache.")

        if cosmosdb_config:
            with optional_import_block() as result:
                from .cosmos_db_cache import CosmosDBCache

            if result.is_successful:
                return CosmosDBCache.create_cache(seed, cosmosdb_config)
            logging.warning("CosmosDBCache is not available. Falling back to DiskCache.")

        path = os.path.join(cache_path_root, str(seed))
        return DiskCache(os.path.join(".", path))