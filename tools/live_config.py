#!/usr/bin/env python3
"""Use live sensor defaults and render the hardware YAML consumed by terminal 1."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
from json import dumps
from pathlib import Path

from ruamel.yaml import YAML

PROJECT = Path(__file__).resolve().parents[1]
OUSTER = "/ouster/os_driver"
PARAMS = "params/ouster.yaml"
YAML_FILE = YAML(typ="safe")


def read_yaml(path: Path) -> dict:
    with path.open(encoding="utf-8") as file:
        return YAML_FILE.load(file)


def write_yaml(path: Path, document: dict) -> None:
    with path.open("w", encoding="utf-8") as file:
        YAML_FILE.dump(document, file)


def hardware_defaults(workspace: Path) -> dict:
    path = workspace / "config/hardware.yaml"
    if not path.is_file():
        path = workspace / "src/vls_bringup/config/hardware.yaml"
    return read_yaml(path)


def prepare(root: Path, workspace: Path, unit: str) -> None:
    if root.exists():
        return
    root.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=root.parent, prefix=".prepare-") as staging:
        profile = Path(staging) / "profile"
        params_dir = profile / "params"
        params_dir.mkdir(parents=True)
        # The upstream launch always supplies these two overrides by default.
        ouster = hardware_defaults(workspace)["ouster"]
        ouster.update(udp_dest="192.168.0.5", use_sim_time=False)
        write_yaml(profile / PARAMS, {OUSTER: {"ros__parameters": ouster}})
        write_yaml(profile / "services.yaml", {"subsystems": {"live_sensors": {
            "unit": unit,
            "param_files": [PARAMS],
            "nodes": [OUSTER],
            "restart_companions": [
                "/velodyne_driver_node", "/velodyne_transform_node",
                "/advanced_navigation_driver_20hz", "/ins_ntrip_relay", "/robot_state_publisher",
            ],
            "topics": [
                {"name": "/os1_cloud_node/points", "expect_hz": 10.0},
                {"name": "/velodyne_points", "expect_hz": 10.0},
                {"name": "/INS/Imu", "expect_hz": 20.0},
            ],
        }}})
        (profile / "schemas").mkdir()
        schema = read_yaml(PROJECT / "config/schemas/ouster_ros.yaml")
        fields = schema["params"]
        for param in ("lidar_port", "imu_port"):
            fields[param]["readback_via"]["json_path"] = f"$.udp_{param}"
        # Host timestamping is handled by the driver even if the sensor clock is unchanged.
        fields["timestamp_mode"]["layer"] = "driver"
        del fields["timestamp_mode"]["readback_via"]
        fields["udp_dest"]["checks"] = ["must_match_host_nic_ip"]
        for param in ("azimuth_window_start", "azimuth_window_end"):
            fields[param]["readback_via"]["json_path"] += (
                ".0" if param.endswith("start") else ".1"
            )
        fields["use_sim_time"] = {"layer": "driver", "apply": "restart", "tier": "read_only"}
        write_yaml(profile / "schemas/ouster_ros.yaml", schema)
        rules = root.parent.parent / "rules"
        shutil.copytree(rules if rules.is_dir() else PROJECT / "config/rules", profile / "rules")
        git = ["git", "-C", str(params_dir)]
        subprocess.run([*git, "init", "-q", "-b", "main"], check=True)
        subprocess.run([*git, "add", "--", "ouster.yaml"], check=True)
        subprocess.run([
            *git, "-c", "user.name=avconfig", "-c", "user.email=avconfig@localhost",
            "commit", "-qm", "initial: terminal 1 sensor configuration",
        ], check=True)
        try:
            profile.rename(root)
        except OSError:
            if not root.is_dir():
                raise


def launch(root: Path, workspace: Path) -> None:
    hardware = hardware_defaults(workspace)
    ouster = read_yaml(root / PARAMS)[OUSTER]["ros__parameters"]
    hardware["ouster"].update(ouster)
    path = root / "hardware.yaml"
    temporary = path.with_suffix(".tmp")
    write_yaml(temporary, hardware)
    temporary.replace(path)
    # This argument otherwise has a fixed launch default that wins over hardware.yaml.
    os.execv("/bin/bash", [
        "bash", str(workspace / "run_live_1_sensors.sh"),
        f"hardware_config:={path}", f"udp_dest:={hardware['ouster']['udp_dest']}",
    ])


def install_unit(root: Path, workspace: Path, unit: str) -> None:
    prepare(root, workspace, unit)
    # systemd expands percent specifiers and dollar-prefixed variables in ExecStart.
    argv = [sys.executable, str(Path(__file__).resolve()), "launch", str(root), str(workspace), unit]
    exec_start = " ".join(dumps(arg.replace("%", "%%").replace("$", "$$"), ensure_ascii=False)
                          for arg in argv)
    environment = {
        name: os.environ[name] for name in (
            "ROS_DOMAIN_ID", "RMW_IMPLEMENTATION", "FASTDDS_BUILTIN_TRANSPORTS",
        ) if name in os.environ
    }
    domain = environment.pop("ROS_DOMAIN_ID")
    environment.update(MAP_LIVE_ROS_DOMAIN_ID=domain, VLS_LIVE_ROS_DOMAIN_ID=domain)
    env_lines = "".join(f"Environment={dumps(f'{key}={value}'.replace('%', '%%'))}\n"
                        for key, value in environment.items())
    path = root / unit
    with tempfile.NamedTemporaryFile(mode="w", dir=root, delete=False, encoding="utf-8") as file:
        file.write(
            "[Unit]\nDescription=Terminal 1 live sensors\n\n"
            "[Service]\nType=exec\nKillMode=mixed\nKillSignal=SIGINT\nTimeoutStopSec=20s\n"
            f"{env_lines}ExecStart={exec_start}\n"
        )
    Path(file.name).replace(path)
    subprocess.run(["systemctl", "--user", "link", str(path)], check=True)
    subprocess.run(["systemctl", "--user", "daemon-reload"], check=True)


if __name__ == "__main__":
    action, root, workspace, unit = sys.argv[1:]
    if action == "prepare":
        prepare(Path(root), Path(workspace), unit)
    elif action == "launch":
        launch(Path(root), Path(workspace))
    elif action == "install-unit":
        install_unit(Path(root), Path(workspace), unit)
    else:
        raise SystemExit(f"Unsupported action: {action}")
