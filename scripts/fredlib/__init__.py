"""Local utilities for portable skills. No agent runtime or network client required.

This package provides programmatic library modules for run lifecycle management,
skill installation, evaluation harnesses, report generation, release packaging,
repository inspection, and static security vetting.
"""

from __future__ import annotations

import importlib
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from . import (
        common,
        evaluations,
        installation,
        releases,
        reports,
        repository,
        runs,
        vetting,
    )

__all__ = [
    "common",
    "evaluations",
    "installation",
    "releases",
    "reports",
    "repository",
    "runs",
    "vetting",
]


def __getattr__(name: str) -> Any:
    if name in __all__:
        module = importlib.import_module(f".{name}", __name__)
        globals()[name] = module
        return module
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def __dir__() -> list[str]:
    return sorted({*globals().keys(), *__all__})
