"""Run the same application contracts through Rust on actual, stock celld."""
import sys
from pathlib import Path

import pytest

from bridge import build_rust, compile_runtime

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "tests"))
import test_celld

pytestmark = test_celld.pytestmark


@pytest.fixture(scope="module", autouse=True)
def compiled():
    compile_runtime()


@pytest.mark.parametrize("contract", [
    test_celld.test_hello_on_actual_celld,
    test_celld.test_shared_fleet_state_concurrency_restart_and_numpy,
    test_celld.test_generated_clients_and_host_context_hook,
], ids=["hello-validation", "numpy-state-replay-concurrency-restart", "generated-clients-context"])
def test_rust_worker_contract(contract, monkeypatch, tmp_path):
    monkeypatch.setattr(test_celld, "build", build_rust)
    contract(tmp_path)
