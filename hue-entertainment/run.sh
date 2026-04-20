#!/bin/sh
set -e
CONFIG=/data/options.json
export BRIDGE_IP=$(python3 -c "import json; print(json.load(open('$CONFIG'))['bridge_ip'])")
export API_KEY=$(python3 -c "import json; print(json.load(open('$CONFIG'))['api_key'])")
export API_PORT=$(python3 -c "import json; print(json.load(open('$CONFIG'))['api_port'])")
export HA_URL=$(python3 -c "import json; print(json.load(open('$CONFIG'))['ha_url'])")
export HA_TOKEN=$(python3 -c "import json; print(json.load(open('$CONFIG'))['ha_token'])")
echo "[INFO] Hue Entertainment Bridge v1.0.0"
echo "[INFO] Bridge: ${BRIDGE_IP}, port: ${API_PORT}"
exec python3 /app/server.py