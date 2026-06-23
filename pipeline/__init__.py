from __future__ import annotations

from importlib import import_module

__all__ = ["Pipeline", "create_shorts_from_video"]


def __getattr__(name: str):
    if name in __all__:
        module = import_module(".highlight", __name__)
        return getattr(module, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")

