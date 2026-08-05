"""Registo de secções. Uma secção = um módulo = uma issue."""

from __future__ import annotations

from types import ModuleType

from . import activity, fees, holdings, income

# A ordem é a ordem de ataque do roadmap.
REGISTRY: dict[str, ModuleType] = {
    holdings.NAME: holdings,
    activity.NAME: activity,
    income.NAME: income,
    fees.NAME: fees,
}

KNOWN_SECTIONS = list(REGISTRY)


def module_for(name: str) -> ModuleType:
    try:
        return REGISTRY[name]
    except KeyError as exc:
        raise KeyError(
            f"secção {name!r} não tem módulo. Conhecidas: {', '.join(KNOWN_SECTIONS)}"
        ) from exc
