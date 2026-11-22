#!/usr/bin/env python3
"""Prepare live sensor profiles and launch terminal 1 with their settings."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
from fcntl import LOCK_EX, flock
from json import dumps
from pathlib import Path

from ruamel.yaml import YAML

PROJECT = Path(__file__).resolve().parents[1]
OUSTER = "/ouster/os_driver"
PARAMS = "params/ouster.yaml"
VELODYNE = "/velodyne_driver_node"
VELODYNE_PARAMS = "params/velodyne.yaml"
INS_DRIVER = "advanced_navigation_driver_20hz"
INS = f"/{INS_DRIVER}"
INS_PARAMS = "params/ins.yaml"
YAML_FILE = YAML(typ="safe")


def read_yaml(path: Path) -> dict:
    with path.open(encoding="utf-8") as file:
        return YAML_FILE.load(file)


def write_yaml(path: Path, document: dict) -> None:
    with path.open("w", encoding="utf-8") as file:
        YAML_FILE.dump(document, file)


def replace_yaml(path: Path, document: dict) -> None:
    with tempfile.NamedTemporaryFile(mode="w", dir=path.parent, delete=False, encoding="utf-8") as file:
        temporary = Path(file.name)
        try:
            YAML_FILE.dump(document, file)
            file.close()
            temporary.replace(path)
        finally:
            temporary.unlink(missing_ok=True)


def hardware_defaults(workspace: Path) -> dict:
    path = workspace / "config/hardware.yaml"
    if not path.is_file():
        path = workspace / "src/vls_bringup/config/hardware.yaml"
    return read_yaml(path)


def seed_schema(root: Path, driver: str) -> None:
    schema = root / "schemas" / f"{driver}.yaml"
    if not schema.exists():
        replace_yaml(schema, read_yaml(PROJECT / "config/schemas" / schema.name))


def seed_velodyne(root: Path, workspace: Path) -> None:
    path = root / VELODYNE_PARAMS
    if not path.exists():
        defaults = dict(hardware_defaults(workspace)["velodyne"], use_sim_time=False)
        replace_yaml(path, {VELODYNE: {"ros__parameters": defaults}})

    seed_schema(root, "velodyne_driver")


def seed_ins(root: Path, workspace: Path) -> None:
    path = root / INS_PARAMS
    if not path.exists():
        relative = Path("advanced_navigation_driver/config/original_20hz.yaml")
        source = workspace / "install/advanced_navigation_driver/share" / relative
        if not source.is_file():
            source = workspace / "install/share" / relative
        if not source.is_file():
            source = workspace / "src" / relative
        document = read_yaml(source)
        defaults = dict(document.get("/**", {}).get("ros__parameters", {}))
        defaults.update(document.get(INS_DRIVER, document.get(INS, {})).get("ros__parameters", {}))
        # In managed mode the NTRIP relay assigns a PTY to the driver at startup.
        defaults.pop("port", None)
        replace_yaml(path, {INS: {"ros__parameters": defaults}})

    seed_schema(root, INS_DRIVER)


def register_driver(subsystems: dict, name: str, node: str, param_file: str) -> None:
    if name in subsystems:
        raise ValueError(f"services.yaml already uses the {name} subsystem name")
    live_sensors = subsystems["live_sensors"]
    subsystems[name] = {
        "unit": live_sensors["unit"],
        "param_files": [param_file],
        "nodes": [node],
    }
    live_sensors["restart_companions"] = [
        companion for companion in live_sensors.get("restart_companions", [])
        if companion.lstrip("/") != node.lstrip("/")
    ]


def migrate_profile(root: Path, workspace: Path) -> None:
    """Register additional drivers while preserving the profile's history."""
    with (root / ".prepare.lock").open("a") as lock:
        flock(lock, LOCK_EX)
        path = root / "services.yaml"
        services = read_yaml(path)
        subsystems = services["subsystems"]
        registered = {
            node.lstrip("/")
            for subsystem in subsystems.values()
            for node in subsystem["nodes"]
        }
        git = ["git", "-C", str(root / "params")]
        for name, node, param_file, seed in (
            ("velodyne_driver", VELODYNE, VELODYNE_PARAMS, seed_velodyne),
            (INS_DRIVER, INS, INS_PARAMS, seed_ins),
        ):
            if node.lstrip("/") in registered:
                continue
            register_driver(subsystems, name, node, param_file)
            seed(root, workspace)
            filename = Path(param_file).name
            if subprocess.run(
                [*git, "cat-file", "-e", f"HEAD:{filename}"], capture_output=True
            ).returncode != 0:
                subprocess.run([*git, "add", "--", filename], check=True)
                subprocess.run([
                    *git, "-c", "user.name=avconfig", "-c", "user.email=avconfig@localhost",
                    "commit", "-qm", f"Register {name} baseline", "--only", "--", filename,
                ], check=True)
            replace_yaml(path, services)


def prepare(root: Path, workspace: Path, unit: str) -> None:
    if root.exists():
        migrate_profile(root, workspace)
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
        subsystems = {"live_sensors": {
            "unit": unit,
            "param_files": [PARAMS],
            "nodes": [OUSTER],
            "restart_companions": [
                "/velodyne_transform_node",
                "/ins_ntrip_relay", "/robot_state_publisher",
            ],
            "topics": [
                {"name": "/os1_cloud_node/points", "expect_hz": 10.0},
                {"name": "/velodyne_points", "expect_hz": 10.0},
                {"name": "/INS/Imu", "expect_hz": 20.0},
            ],
        }}
        register_driver(subsystems, "velodyne_driver", VELODYNE, VELODYNE_PARAMS)
        register_driver(subsystems, INS_DRIVER, INS, INS_PARAMS)
        write_yaml(profile / "services.yaml", {"subsystems": subsystems})
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
        seed_velodyne(profile, workspace)
        seed_ins(profile, workspace)
        rules = root.parent.parent / "rules"
        shutil.copytree(rules if rules.is_dir() else PROJECT / "config/rules", profile / "rules")
        git = ["git", "-C", str(params_dir)]
        subprocess.run([*git, "init", "-q", "-b", "main"], check=True)
        subprocess.run([
            *git, "add", "--", Path(PARAMS).name, Path(VELODYNE_PARAMS).name, Path(INS_PARAMS).name,
        ], check=True)
        subprocess.run([
            *git, "-c", "user.name=avconfig", "-c", "user.email=avconfig@localhost",
            "commit", "-qm", "initial: terminal 1 sensor configuration",
        ], check=True)
        try:
            profile.rename(root)
        except OSError:
            if not root.is_dir():
                raise


def render_hardware(root: Path, workspace: Path) -> dict:
    hardware = hardware_defaults(workspace)
    ouster = read_yaml(root / PARAMS)[OUSTER]["ros__parameters"]
    hardware["ouster"].update(ouster)
    # Honor profiles created before Velodyne registration until they migrate.
    velodyne_path = root / VELODYNE_PARAMS
    if velodyne_path.is_file():
        hardware["velodyne"].update(read_yaml(velodyne_path)[VELODYNE]["ros__parameters"])
    return hardware


def launch(root: Path, workspace: Path) -> None:
    hardware = render_hardware(root, workspace)
    path = root / "hardware.yaml"
    replace_yaml(path, hardware)
    # This argument otherwise has a fixed launch default that wins over hardware.yaml.
    argv = [
        "bash", str(workspace / "run_live_1_sensors.sh"),
        f"hardware_config:={path}", f"udp_dest:={hardware['ouster']['udp_dest']}",
    ]
    ins_path = root / INS_PARAMS
    if ins_path.is_file():
        argv.append(f"ins_config:={ins_path}")
    os.execv("/bin/bash", argv)


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
