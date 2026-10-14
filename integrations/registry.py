"""Adapter registry. Adding a system = one new module in integrations/adapters/."""

import asyncio
import importlib
import pkgutil

from .base import Adapter

_adapters: dict[str, Adapter] = {}


def register(cls: type[Adapter]) -> type[Adapter]:
    _adapters[cls.name] = cls()
    return cls


def unregister(name: str):
    _adapters.pop(name, None)


def get(name: str) -> Adapter:
    return _adapters[name]


def panels() -> list[Adapter]:
    return sorted((a for a in _adapters.values() if a.shows_in_sidebar), key=lambda a: a.panel_order)


def autodiscover():
    from . import adapters

    for module in pkgutil.iter_modules(adapters.__path__):
        importlib.import_module(f"{adapters.__name__}.{module.name}")


def reset_state():
    for adapter in _adapters.values():
        adapter.reset_state()


async def drain():
    await asyncio.gather(*(a.drain() for a in _adapters.values()))
