#!/bin/bash
# Download YOUR ZED's factory calibration (required for depth). Serial is on the camera / ZED tools.
SN="$1"; D="$(cd "$(dirname "$0")" && pwd)"
[ -z "$SN" ] && { echo "usage: bash utils/get_calibration.sh <ZED_SERIAL>"; exit 1; }
curl -sL "https://calib.stereolabs.com/?SN=$SN" -o "$D/calib.conf" && echo "saved $D/calib.conf" && head -1 "$D/calib.conf"
