#!/bin/bash
set -e
apt-get update && apt-get install -y curl
curl -LsSf https://astral.sh/uv/0.7.13/install.sh | sh
source $HOME/.local/bin/env

if [ "$PWD" = "/" ]; then
    echo "Error: No working directory set."
    exit 1
fi

uv venv .loopsbench-testing
source .loopsbench-testing/bin/activate
uv pip install pytest==8.4.1
TEST_DIR="${TEST_DIR:-/tests}"

echo "[test] Testing: vfdt"
uv run pytest "${TEST_DIR}/vfdt/test_outputs.py" -rA
echo "[test] Testing: cvfdt"
uv run pytest "${TEST_DIR}/cvfdt/test_outputs.py" -rA
echo "[test] Testing: adwin"
uv run pytest "${TEST_DIR}/adwin/test_outputs.py" -rA
echo "[test] Testing: moa"
uv run pytest "${TEST_DIR}/moa/test_outputs.py" -rA
echo "[test] Testing: sam_knn"
uv run pytest "${TEST_DIR}/sam_knn/test_outputs.py" -rA
