"""Apply the finite reviewed no-window selection before fixture execution."""
from pathlib import Path
import json

import pytest

from scripts import test_entrypoint_audit as audit


def pytest_addoption(parser):
    parser.addoption("--audit-window-inventory", action="store_true",
                     help="collection only: retain cancelled identities for inventory reconciliation")


@pytest.hookimpl(tryfirst=True)
def pytest_collection_modifyitems(config, items):
    retain = config.getoption("--audit-window-inventory")
    if retain and not config.option.collectonly:
        raise pytest.UsageError("cancelled window identities may only be inventoried, never executed")
    registry = json.loads(audit.REGISTRY_PATH.read_text(encoding="utf-8"))
    if registry.get("policy") != audit.POLICY:
        raise pytest.UsageError("unknown active validation policy")
    registration = audit.expand_registry(registry)
    active, cancelled = [], []
    for item in items:
        # Tiny subprocess fixtures outside tests own their explicit registry.
        if not Path(item.path).resolve().is_relative_to(audit.ROOT / "tests"):
            active.append(item)
            continue
        parts = item.nodeid.split("::")
        identity = ".".join([Path(parts[0]).stem, *parts[1:]])
        if identity not in registration:
            raise pytest.UsageError("unregistered collected identity: " + identity)
        if registration[identity] == "gui" and not retain:
            cancelled.append(item)
        else:
            active.append(item)
    items[:] = active
    config.hook.pytest_deselected(items=cancelled)
