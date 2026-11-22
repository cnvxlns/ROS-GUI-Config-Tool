#!/usr/bin/env bash
# Resolve the existing sensor workspace without sourcing ROS or opening devices.
av_config_select_sensor_workspace() {
    local av_config_requested=${AV_CONFIG_SENSOR_WORKSPACE:-}
    local av_config_candidate av_config_project
    av_config_project=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd -P)

    case "$av_config_requested" in
        map) av_config_requested="$HOME/map_ws" ;;
        vls) av_config_requested="$HOME/Seonguk/vls_ws" ;;
        '')
            for av_config_candidate in "$HOME/map_ws" "$HOME/Seonguk/vls_ws"; do
                if [[ -f "$av_config_candidate/run_live_1_sensors.sh" &&
                      -f "$av_config_candidate/config/live_env.sh" ]]; then
                    av_config_requested=$av_config_candidate
                    break
                fi
            done
            ;;
    esac

    if [[ ! -f "$av_config_requested/run_live_1_sensors.sh" ||
          ! -f "$av_config_requested/config/live_env.sh" ]]; then
        printf '%s\n' \
            'Sensor workspace not found. Set AV_CONFIG_SENSOR_WORKSPACE=map, vls, or an existing workspace path.' >&2
        return 2
    fi
    av_config_requested=$(cd -- "$av_config_requested" && pwd -P) || return
    if [[ $av_config_requested == "$av_config_project" ]]; then
        printf 'Select map_ws or vls_ws, not this wrapper project.\n' >&2
        return 2
    fi
    export AV_CONFIG_SENSOR_WORKSPACE=$av_config_requested
}

# Keep each workspace/domain's settings and restart target paired.
av_config_live_profile() {
    local workspace_id unit_id
    workspace_id=$(printf '%s' "$AV_CONFIG_SENSOR_WORKSPACE" | sha256sum) || return
    local profile="$(basename -- "$AV_CONFIG_SENSOR_WORKSPACE")-${workspace_id:0:12}-$ROS_DOMAIN_ID"
    export AV_CONFIG_LIVE_ROOT="$(realpath -m -- "${AV_CONFIG_ROOT:-$HOME/av_config}/live/$profile")"
    unit_id=$(printf '%s' "$AV_CONFIG_LIVE_ROOT" | sha256sum) || return
    export AV_CONFIG_LIVE_UNIT="av-live-sensors-${unit_id:0:12}-$ROS_DOMAIN_ID.service"
}
