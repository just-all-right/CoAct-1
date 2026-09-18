import sys
from types import TracebackType
from typing import Any, Optional, Protocol

from ..doc_utils import export_module

if sys.version_info >= (3, 11):
    from typing import Self
else:
    from typing_extensions import Self


@export_module("autogen.cache")
class AbstractCache(Protocol):
    def get(self, key: str, default: Optional[Any] = None) -> Optional[Any]: ...

    def set(self, key: str, value: Any) -> None: ...

    def close(self) -> None: ...

    def __enter__(self) -> Self: ...

    def __exit__(
        self,
        exc_type: Optional[type[BaseException]],
        exc_value: Optional[BaseException],
        traceback: Optional[TracebackType],
    ) -> None: ...