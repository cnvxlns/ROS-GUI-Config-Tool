"""Real ROS diagnostic byte fields must not stop the parameter-service executor."""

from __future__ import annotations

import sys
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

pytest.importorskip("rclpy")
diagnostic_msgs = pytest.importorskip("diagnostic_msgs.msg")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from av_config.verifier import DiagnosticsCache, Verifier  # noqa: E402


@pytest.mark.parametrize("name, expected", [("OK", 0), ("WARN", 1), ("ERROR", 2), ("STALE", 3)])
def test_generated_ros_diagnostic_levels(name, expected):
    status = diagnostic_msgs.DiagnosticStatus(
        level=getattr(diagnostic_msgs.DiagnosticStatus, name),
        name="Velodyne driver",
        hardware_id="velodyne",
        message="sensor report",
    )
    cache = DiagnosticsCache()
    cache.update(diagnostic_msgs.DiagnosticArray(status=[status]))
    assert cache.worst(["velodyne"]) == (expected, "sensor report")

    verifier = Verifier.__new__(Verifier)
    verifier.diagnostics = cache
    if expected == 0:
        assert verifier.diagnostics_ok(["velodyne"]) == (True, None)
    else:
        assert verifier.diagnostics_ok(["velodyne"]) == (
            False, f"/diagnostics {name}: sensor report"
        )


def test_integer_levels_and_name_fallback():
    cache = DiagnosticsCache()
    cache.update(SimpleNamespace(status=[SimpleNamespace(
        level=2, hardware_id="", name="sensor", message="error"
    )]))
    assert cache.worst(["sensor"]) == (2, "error")


def test_stale_diagnostic_compares_with_fresh_integer_levels():
    cache = DiagnosticsCache(by_name={
        "stale": (0, "old", time.time() - 60),
        "fresh": (2, "error", time.time()),
    })
    for keys in (["stale", "fresh"], ["fresh", "stale"]):
        assert cache.worst(keys) == (3, "STALE")
    verifier = Verifier.__new__(Verifier)
    verifier.diagnostics = cache
    assert verifier.diagnostics_ok(["stale"]) == (False, "/diagnostics STALE: STALE")
