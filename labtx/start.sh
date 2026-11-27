#!/usr/bin/env bash
set -eo pipefail

project_dir="$(cd "$(dirname "$0")/.." && pwd)"
source /opt/ros/humble/setup.bash
export PYTHONPATH="$project_dir${PYTHONPATH:+:$PYTHONPATH}"
exec python3 -m labtx "$@"

