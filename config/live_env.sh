#!/usr/bin/env bash
# Source this in the app/inspection terminal to join the sensor launch's domain.
av_config_live_project=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
source "$av_config_live_project/tools/live_workspace.sh"
av_config_select_sensor_workspace || return
source "$AV_CONFIG_SENSOR_WORKSPACE/config/live_env.sh" || return
# Keep the selected driver's dependencies and service types visible to the app.
export PYTHONPATH="$av_config_live_project/src/av_config${PYTHONPATH:+:$PYTHONPATH}"
export ROS_LOG_DIR="$av_config_live_project/log/ros"
unset av_config_live_project
