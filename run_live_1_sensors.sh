#!/usr/bin/env bash
set -eo pipefail
av_config_project=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
if [[ ${1:-} == --help || ${1:-} == -h ]]; then
    cat <<'USAGE'
Usage: ./run_live_1_sensors.sh [--select] [--dry-run] [name:=value ...]
       ./run_live_1_sensors.sh --managed | --stop

Run the existing terminal 1 command: Ouster + Velodyne + INS/NTRIP + sensor TF.
All arguments and the original sensor/NTRIP defaults are passed through.
AV_CONFIG_SENSOR_WORKSPACE=map (default when available), vls, or a workspace path.
Use the same workspace selection with ./run_config_app.sh in another terminal.
Stop the sensors with Ctrl+C in this terminal.
--managed starts the same sensor launch under user systemd for web changes.
Stop that launch with --stop, and read its log with journalctl --user -fu UNIT.
USAGE
    exit 0
fi
source "$av_config_project/tools/live_workspace.sh"
av_config_select_sensor_workspace
printf 'Sensor workspace: %s\n' "$AV_CONFIG_SENSOR_WORKSPACE" >&2
if [[ ${1:-} == --managed || ${1:-} == --stop ]]; then
    if (( $# != 1 )); then
        printf 'Pass --managed or --stop on its own.\n' >&2
        exit 2
    fi
    source "$av_config_project/config/live_env.sh"
    av_config_live_profile
    if [[ $1 == --stop ]]; then
        exec systemctl --user stop "$AV_CONFIG_LIVE_UNIT"
    fi
    if systemctl --user is-active --quiet "$AV_CONFIG_LIVE_UNIT"; then
        printf 'Sensor unit already running: %s\n' "$AV_CONFIG_LIVE_UNIT"
        exit 0
    fi
    live_nodes=$(timeout 8s ros2 node list --no-daemon)
    if grep -Eqx '/(ouster/os_driver|velodyne_driver_node|advanced_navigation_driver_20hz|ins_ntrip_relay)' <<< "$live_nodes"; then
        printf 'Sensors are already running in ROS domain %s. Stop that launch before using --managed.\n' "$ROS_DOMAIN_ID" >&2
        exit 1
    fi
    av_config_python=${AV_CONFIG_PYTHON:-"$av_config_project/.venv/bin/python"}
    "$av_config_python" "$av_config_project/tools/live_config.py" install-unit \
        "$AV_CONFIG_LIVE_ROOT" "$AV_CONFIG_SENSOR_WORKSPACE" "$AV_CONFIG_LIVE_UNIT"
    systemctl --user start "$AV_CONFIG_LIVE_UNIT"
    printf 'Sensor unit: %s\n' "$AV_CONFIG_LIVE_UNIT"
    exit 0
fi
exec bash "$AV_CONFIG_SENSOR_WORKSPACE/run_live_1_sensors.sh" "$@"
