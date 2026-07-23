#!/bin/bash
# One-time after clone: fetch RAFT-Stereo repo + pretrained weights (large, not committed).
D="$(cd "$(dirname "$0")" && pwd)"
[ -d "$D/RAFT-Stereo/core" ] || git clone --depth 1 https://github.com/princeton-vl/RAFT-Stereo.git "$D/RAFT-Stereo"
mkdir -p "$D/RAFT-Stereo/models"; cd "$D/RAFT-Stereo/models"
[ -f raftstereo-middlebury.pth ] || { curl -L -o models.zip "https://www.dropbox.com/s/ftveifyqcomiwaq/models.zip?dl=1" && unzip -o models.zip && rm -f models.zip; }
echo "RAFT-Stereo ready. Next: bash utils/get_calibration.sh <ZED serial>"
