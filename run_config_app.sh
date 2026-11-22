#!/usr/bin/env bash
set -eo pipefail
av_config_project=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
if [[ ${1:-} == --help || ${1:-} == -h ]]; then
    cat <<'USAGE'
Usage: ./run_config_app.sh [--dry-run]

Start the web app in the existing live sensor workspace's ROS environment.
AV_CONFIG_SENSOR_WORKSPACE=map (default when available), vls, or a workspace path.
Live profiles are stored under AV_CONFIG_ROOT/live (default: ~/av_config/live).
AV_CONFIG_HOST/PORT defaults to 127.0.0.1:8080.
AV_CONFIG_PYTHON defaults to this project's .venv/bin/python.
--dry-run prints the environment and command without starting the app.
USAGE
    exit 0
fi
if (( $# > 1 )) || { (( $# == 1 )) && [[ $1 != --dry-run ]]; }; then
    printf 'Usage: ./run_config_app.sh [--dry-run]\n' >&2
    exit 2
fi
av_config_python=${AV_CONFIG_PYTHON:-"$av_config_project/.venv/bin/python"}
if [[ ! -x $av_config_python ]]; then
    printf 'Python environment not found: %s. Follow the native setup in README.md.\n' "$av_config_python" >&2
    exit 2
fi
export AV_CONFIG_HOST=${AV_CONFIG_HOST:-127.0.0.1}
export AV_CONFIG_PORT=${AV_CONFIG_PORT:-8080}
source "$av_config_project/config/live_env.sh"
av_config_live_profile
export AV_CONFIG_ROOT=$AV_CONFIG_LIVE_ROOT
export AV_CONFIG_SYSTEMCTL_USER=1
# The sensor dependency cache includes Pydantic 1; the web app needs its venv's
# Pydantic 2 first, while retaining ROS libraries and driver service types.
av_config_site_packages=$("$av_config_python" -c 'import sysconfig; print(sysconfig.get_path("purelib"))')
export PYTHONPATH="$av_config_project/src/av_config:$av_config_site_packages${PYTHONPATH:+:$PYTHONPATH}"
printf 'Sensor workspace: %s\nROS_DOMAIN_ID=%s; discovery=%s\nConfig root: %s\nSensor unit: %s\nWeb: http://%s:%s/\n' \
    "$AV_CONFIG_SENSOR_WORKSPACE" "$ROS_DOMAIN_ID" "$ROS_AUTOMATIC_DISCOVERY_RANGE" \
    "$AV_CONFIG_ROOT" "$AV_CONFIG_LIVE_UNIT" "$AV_CONFIG_HOST" "$AV_CONFIG_PORT"
if [[ ${1:-} == --dry-run ]]; then
    printf 'PREVIEW ONLY: '
    printf '%q ' "$av_config_python" -m av_config.main
    printf '\n'
    exit 0
fi
"$av_config_python" "$av_config_project/tools/live_config.py" prepare \
    "$AV_CONFIG_ROOT" "$AV_CONFIG_SENSOR_WORKSPACE" "$AV_CONFIG_LIVE_UNIT"
exec "$av_config_python" -m av_config.main
