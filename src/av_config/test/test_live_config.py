"""Verify driver registration for new and existing terminal 1 profiles."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

PROJECT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(PROJECT))
sys.path.insert(0, str(PROJECT / "src/av_config"))

from av_config.services_map import ServicesMap  # noqa: E402
from av_config.schema import SchemaRegistry  # noqa: E402
from av_config.store import ParamStore, ParamWriter  # noqa: E402
from tools import live_config  # noqa: E402

UNIT = "av-live-sensors-test.service"


@pytest.fixture
def workspace(tmp_path):
    root = tmp_path / "workspace"
    (root / "config").mkdir(parents=True)
    live_config.write_yaml(root / "config/hardware.yaml", {
        "ouster": {"lidar_mode": "1024x20"},
        "velodyne": {"device_ip": "192.168.1.201", "port": 2368, "rpm": 600.0},
        "velodyne_cloud": {"model": "VLP16"},
    })
    ins_config = root / "src/advanced_navigation_driver/config/original_20hz.yaml"
    ins_config.parent.mkdir(parents=True)
    live_config.write_yaml(ins_config, {live_config.INS_DRIVER: {"ros__parameters": {
        "port": "/dev/ttyUSB0", "baud_rate": 1000000, "topic_prefix": "/INS",
    }}})
    return root


def git(root, *args):
    return subprocess.check_output(["git", "-C", str(root / "params"), *args], text=True).strip()


def test_registered_velodyne_writes_to_the_file_read_by_sensor_launch(tmp_path, workspace):
    profile = tmp_path / "profile"
    live_config.prepare(profile, workspace, UNIT)
    services = ServicesMap.load(profile / "services.yaml")
    node = live_config.VELODYNE
    assert services.unit_of(node) == UNIT
    assert services.param_files_of(node) == ["params/velodyne.yaml"]
    assert services.nodes_in_unit(UNIT).count(node) == 1

    writer = ParamWriter(ParamStore(profile))
    targets = writer.plan_targets(node, {"port": 2369, "cut_angle": 1.0}, services.param_files_of(node))
    assert writer.apply(targets) == ["params/velodyne.yaml"]
    hardware = live_config.render_hardware(profile, workspace)
    assert hardware["velodyne"]["port"] == 2369
    assert hardware["velodyne"]["cut_angle"] == 1.0
    assert hardware["velodyne_cloud"] == {"model": "VLP16"}
    assert live_config.hardware_defaults(workspace)["velodyne"]["port"] == 2368


def test_registered_ins_writes_to_the_file_read_by_sensor_launch(tmp_path, workspace, monkeypatch):
    profile = tmp_path / "profile"
    live_config.prepare(profile, workspace, UNIT)
    services = ServicesMap.load(profile / "services.yaml")
    node = live_config.INS
    assert services.unit_of(node) == UNIT
    assert services.param_files_of(node) == [live_config.INS_PARAMS]
    assert services.nodes_in_unit(UNIT).count(node) == 1

    store = ParamStore(profile)
    saved = store.saved_values(node, services.param_files_of(node))
    assert saved["baud_rate"].value == 1000000
    assert "port" not in saved
    schemas = SchemaRegistry.load_dir(profile / "schemas")
    schema = schemas.match(node, [])
    assert schema.params["port"].tier == "read_only"

    writer = ParamWriter(store)
    targets = writer.plan_targets(node, {"baud_rate": 921600}, services.param_files_of(node))
    assert writer.apply(targets) == [live_config.INS_PARAMS]
    argv = []
    monkeypatch.setattr(live_config.os, "execv", lambda _, args: argv.extend(args))
    live_config.launch(profile, workspace)
    assert f"ins_config:={profile / live_config.INS_PARAMS}" in argv
    assert store.saved_values(node, services.param_files_of(node))["baud_rate"].value == 921600


@pytest.mark.parametrize("has_velodyne", [False, True])
def test_existing_profile_migrates_once_and_keeps_ouster_history(tmp_path, workspace, has_velodyne):
    profile = tmp_path / "profile"
    (profile / "params").mkdir(parents=True)
    (profile / "schemas").mkdir()
    old_settings = {live_config.OUSTER: {"ros__parameters": {"lidar_mode": "512x20"}}}
    live_config.write_yaml(profile / live_config.PARAMS, old_settings)
    subsystems = {"live_sensors": {
        "unit": UNIT,
        "nodes": [live_config.OUSTER],
        "param_files": [live_config.PARAMS],
        "restart_companions": [live_config.VELODYNE, live_config.INS, "/velodyne_transform_node"],
    }}
    git(profile, "init", "-q")
    git(profile, "add", "ouster.yaml")
    if has_velodyne:
        live_config.register_driver(subsystems, "velodyne_driver", live_config.VELODYNE,
                                    live_config.VELODYNE_PARAMS)
        live_config.seed_velodyne(profile, workspace)
        git(profile, "add", "velodyne.yaml")
    live_config.write_yaml(profile / "services.yaml", {"subsystems": subsystems})
    git(profile, "-c", "user.name=test", "-c", "user.email=test@example.org", "commit", "-qm", "old")
    old_head = git(profile, "rev-parse", "HEAD")

    live_config.prepare(profile, workspace, UNIT)
    new_head = git(profile, "rev-parse", "HEAD")
    expected_files = ["ins.yaml"] if has_velodyne else ["ins.yaml", "velodyne.yaml"]
    assert git(profile, "diff", "--name-only", old_head, new_head).splitlines() == expected_files
    assert live_config.read_yaml(profile / live_config.PARAMS) == old_settings
    services = ServicesMap.load(profile / "services.yaml")
    assert services.nodes_in_unit(UNIT).count(live_config.VELODYNE) == 1
    assert services.nodes_in_unit(UNIT).count(live_config.INS) == 1
    assert services.param_files_of(live_config.INS) == [live_config.INS_PARAMS]
    assert "/velodyne_transform_node" in services.nodes_in_unit(UNIT)
    live_config.prepare(profile, workspace, UNIT)
    assert git(profile, "rev-parse", "HEAD") == new_head
    assert git(profile, "status", "--short") == ""
