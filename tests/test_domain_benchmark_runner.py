import importlib.util
from pathlib import Path

import pytest


def _benchmark_module():
    path = (
        Path(__file__).resolve().parents[1]
        / "storage"
        / "evaluations"
        / "run-domain-benchmark-100.py"
    )
    spec = importlib.util.spec_from_file_location("domain_benchmark_runner", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_select_cases_keeps_definition_order_and_rejects_unknown_ids():
    module = _benchmark_module()
    cases = [{"id": "a"}, {"id": "b"}, {"id": "c"}]

    assert module.select_cases(cases, ["c", "a"]) == [cases[0], cases[2]]
    with pytest.raises(ValueError, match="unknown benchmark case IDs: missing"):
        module.select_cases(cases, ["missing"])
