"""Smoke tests ensuring all stellar_paygate package modules import cleanly."""

from __future__ import annotations

import importlib

import pytest

PACKAGE_MODULES = [
    "stellar_paygate",
    "stellar_paygate.types",
    "stellar_paygate.codec",
    "stellar_paygate.exact",
    "stellar_paygate.client",
    "stellar_paygate.fastapi_ext",
    "stellar_paygate.fault_harness",
]


@pytest.mark.parametrize("module_name", PACKAGE_MODULES)
def test_module_imports(module_name: str) -> None:
    module = importlib.import_module(module_name)
    assert module.__name__ == module_name
