#!/usr/bin/env bash
set -Eeuo pipefail

PROJECT_DIR="$(cd -- "$(dirname -- "$0")" && pwd)"
VENV_DIR="$PROJECT_DIR/.venv"
PYTHON_BIN="/usr/bin/python3.13"
LOCK_FILE="$PROJECT_DIR/requirements-pi.lock"

if [[ "$(uname -m)" != "aarch64" ]]; then
    echo "This rebuild script targets 64-bit ARM (aarch64)." >&2
    exit 1
fi

if [[ ! -x "$PYTHON_BIN" ]]; then
    echo "Python 3.13 is required at $PYTHON_BIN." >&2
    exit 1
fi

"$PYTHON_BIN" - <<'PY'
import importlib.util
import sys

if sys.version_info[:2] != (3, 13):
    raise SystemExit("VeriLock Pi dependencies are pinned for Python 3.13.")

missing = [
    name for name in ("tkinter", "picamera2", "gpiozero", "lgpio")
    if importlib.util.find_spec(name) is None
]
if missing:
    raise SystemExit(
        "Missing Raspberry Pi OS modules: "
        + ", ".join(missing)
        + ". Install the packages listed in PI_OS_PACKAGES.txt first."
    )
PY

if command -v systemctl >/dev/null 2>&1 &&
   systemctl is-active --quiet verilock.service; then
    echo "Stop verilock.service before rebuilding its virtual environment." >&2
    exit 1
fi

if [[ ! -f "$LOCK_FILE" ]]; then
    if [[ ! -x "$VENV_DIR/bin/python" ]]; then
        echo "Dependency lock is missing and no working .venv is available. Restore requirements-pi.lock before rebuilding." >&2
        exit 1
    fi

    FREEZE_TMP="$LOCK_FILE.capture.$$"
    LOCK_TMP="$LOCK_FILE.tmp.$$"
    cleanup_capture() { rm -f -- "$FREEZE_TMP" "$LOCK_TMP"; }
    trap cleanup_capture EXIT

    # Validate the installed environment before using it as a recovery source.
    "$VENV_DIR/bin/python" -m pip check
    "$VENV_DIR/bin/python" -m pip freeze --local > "$FREEZE_TMP"
    "$PYTHON_BIN" - "$PROJECT_DIR/requirements.txt" "$FREEZE_TMP" <<'PYVALIDATE'
import re
import sys
from pathlib import Path

def normalize(name):
    return re.sub(r"[-_.]+", "-", name).lower()

pin = re.compile(r"([A-Za-z0-9][A-Za-z0-9._-]*)==([A-Za-z0-9][A-Za-z0-9.!+_-]*)")

def parse_pin(line):
    match = pin.fullmatch(line)
    if match is None:
        raise SystemExit("Recovery requires exact package pins; invalid entry: " + line)
    return normalize(match[1]), match[2]

# Recover the missing lock from installed packages rather than reading it recursively.
required = {}
for raw in Path(sys.argv[1]).read_text().splitlines():
    line = raw.strip()
    if not line or line.startswith("#"):
        continue
    if line == "-r requirements-pi.lock":
        continue
    name, version = parse_pin(line)
    if name in required and required[name] != version:
        raise SystemExit("Conflicting required pins: " + name)
    required[name] = version

installed = {}
for raw in Path(sys.argv[2]).read_text().splitlines():
    line = raw.strip()
    if not line or line.startswith("#"):
        continue
    name, version = parse_pin(line)
    if name in installed:
        raise SystemExit("Duplicate captured package: " + name)
    installed[name] = version

# pip freeze --local intentionally excludes Raspberry Pi OS packages.
core = {
    "adafruit-blinka", "adafruit-circuitpython-pn532", "customtkinter",
    "insightface", "numpy", "onnxruntime", "pillow",
}
missing = core - installed.keys()
opencv_names = {
    "opencv-python", "opencv-python-headless", "opencv-contrib-python",
    "opencv-contrib-python-headless",
}
if installed.keys() & opencv_names != {"opencv-python-headless"}:
    raise SystemExit("Recovery requires opencv-python-headless only. Restore the current project lock instead of capturing an overlapping environment.")
if missing:
    raise SystemExit("Recovery source is missing application packages: " + ", ".join(sorted(missing)))
for name, version in required.items():
    if installed.get(name) != version:
        raise SystemExit("Recovery source does not match required pin: " + name + "==" + version)
PYVALIDATE

    {
        echo "# Recovered from the existing Pi virtual environment before rebuilding."
        echo "# Target: Raspberry Pi OS Trixie, aarch64, Python 3.13."
        cat "$FREEZE_TMP"
    } > "$LOCK_TMP"
    mv -- "$LOCK_TMP" "$LOCK_FILE"
    cleanup_capture
    trap - EXIT
    echo "Recovered requirements-pi.lock from the installed package set."
fi

OLD_DIR="$PROJECT_DIR/.venv.pre-rebuild.$$"
OLD_MOVED=0
NEW_ENV_STARTED=0

restore_old_environment() {
    result=$?
    if [[ "$result" -ne 0 ]]; then
        # A failed backup move must leave the original .venv untouched.
        if [[ "$NEW_ENV_STARTED" -eq 1 ]]; then
            if ! rm -rf -- "$VENV_DIR"; then
                echo "Could not remove the incomplete environment. Backup retained at $OLD_DIR." >&2
                return "$result"
            fi
        fi
        if [[ "$OLD_MOVED" -eq 1 && -d "$OLD_DIR" ]]; then
            if mv -- "$OLD_DIR" "$VENV_DIR"; then
                echo "Restored the previous virtual environment." >&2
            else
                echo "Could not restore the previous environment. Backup retained at $OLD_DIR." >&2
            fi
        fi
    fi
    return "$result"
}
trap restore_old_environment EXIT

if [[ -e "$VENV_DIR" ]]; then
    mv -- "$VENV_DIR" "$OLD_DIR"
    OLD_MOVED=1
fi

# Only remove an environment that this rebuild has started creating.
NEW_ENV_STARTED=1
"$PYTHON_BIN" -m venv --system-site-packages "$VENV_DIR"
"$VENV_DIR/bin/python" -m pip install --disable-pip-version-check --upgrade "pip==25.1.1"

if [[ -f "$LOCK_FILE" ]]; then
    echo "Installing the target-specific lock file."
    "$VENV_DIR/bin/python" -m pip install --disable-pip-version-check -r "$LOCK_FILE"
else
    echo "Installing the pinned application requirements."
    "$VENV_DIR/bin/python" -m pip install --disable-pip-version-check -r "$PROJECT_DIR/requirements.txt"
fi

"$VENV_DIR/bin/python" -m pip check
"$VENV_DIR/bin/python" - <<'PY'
import adafruit_pn532.i2c
import board
import busio
import cv2
import customtkinter
import digitalio
import gpiozero
import insightface
import lgpio
import numpy
import onnxruntime
import picamera2
import PIL
import tkinter

from importlib import metadata
from pathlib import Path
import re
import sys

opencv_names = {
    "opencv-python", "opencv-python-headless", "opencv-contrib-python",
    "opencv-contrib-python-headless",
}
installed = {
    re.sub(r"[-_.]+", "-", distribution.metadata["Name"]).lower()
    for distribution in metadata.distributions()
    if distribution.metadata.get("Name")
}
if installed & opencv_names != {"opencv-python-headless"}:
    raise SystemExit("Expected exactly one OpenCV distribution: opencv-python-headless.")
if not Path(cv2.__file__).resolve().is_relative_to(Path(sys.prefix).resolve()):
    raise SystemExit("cv2 is not loading from the rebuilt virtual environment.")

providers = onnxruntime.get_available_providers()
if "CPUExecutionProvider" not in providers:
    raise SystemExit(f"ONNX Runtime CPU provider missing: {providers}")

print(f"OpenCV {cv2.__version__}")
print(f"NumPy {numpy.__version__}")
print(f"ONNX Runtime {onnxruntime.__version__}; providers: {providers}")
print("VeriLock Pi imports passed.")
PY

if [[ ! -f "$LOCK_FILE" ]]; then
    LOCK_TMP="$LOCK_FILE.tmp.$$"
    {
        echo "# Generated by rebuild_venv.sh after successful import checks."
        echo "# Target: Raspberry Pi OS Trixie, aarch64, Python 3.13."
        "$VENV_DIR/bin/python" -m pip freeze --local
    } > "$LOCK_TMP"
    mv -- "$LOCK_TMP" "$LOCK_FILE"
    echo "Wrote the resolved pip dependency lock to $LOCK_FILE."
fi

# Keep the rebuilt environment if removing its old backup fails after validation.
trap - EXIT
if [[ "$OLD_MOVED" -eq 1 ]]; then
    rm -rf -- "$OLD_DIR"
    OLD_MOVED=0
fi

trap - EXIT
echo "Virtual environment rebuilt at $VENV_DIR."
