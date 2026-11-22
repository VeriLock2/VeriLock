"""Settings for VeriLock hardware, authentication, and the user interface."""


import platform
from pathlib import Path


# Leave this as None to detect the Pi automatically, or choose a mode for testing.
SIMULATION_OVERRIDE = None


def _running_on_raspberry_pi():
    """Check whether this computer appears to be a Raspberry Pi."""
    try:
        model_file = Path("/proc/device-tree/model")
        if model_file.exists():
            model = model_file.read_text(
                encoding="utf-8",
                errors="ignore"
            ).lower()
            if "raspberry pi" in model:
                return True
    except Exception:
        pass

    # If the usual model file is missing, check the Linux device tree instead.
    machine = platform.machine().lower()
    system = platform.system().lower()

    return (
        system == "linux"
        and machine in {"aarch64", "arm64", "armv7l", "armv6l"}
        and Path("/sys/firmware/devicetree/base").exists()
    )


RUNNING_ON_RASPBERRY_PI = _running_on_raspberry_pi()

if SIMULATION_OVERRIDE is None:
    SIMULATION_MODE = not RUNNING_ON_RASPBERRY_PI
else:
    SIMULATION_MODE = bool(SIMULATION_OVERRIDE)

PLATFORM_NAME = (
    "RASPBERRY PI"
    if RUNNING_ON_RASPBERRY_PI and not SIMULATION_MODE
    else "SIMULATION"
)
# Settings for the Pi hardware and touchscreen.
FULLSCREEN = False
HIDE_CURSOR = True
# Indices follow the active output list; the current touchscreen is index 0.
# On the current Pi, wlr-randr lists HDMI-A-1 (touchscreen) before HDMI-A-2 (monitor).
DISPLAY_MONITOR_INDEX = 0

# These are BCM GPIO numbers, not physical header pin numbers.
SERVO_GPIO = 18          # Physical pin 12.
RACK_SERVO_GPIO = 13      # Physical pin 33. Make sure nothing else is using it.

# The PN532 reader uses I2C bus 1.
RFID_I2C_BUS = 1
RFID_I2C_ADDRESS = 0x24   # The PN532's default I2C address.
RFID_SDA_GPIO = 2        # Physical pin 3.
RFID_SCL_GPIO = 3        # Physical pin 5.
RFID_RESET_GPIO = 6      # Physical pin 31 goes to PN532 RSTPD_N.
RFID_REQUEST_GPIO = 12   # Physical pin 32 goes to PN532 P32/H_Request.


BASE_DIR = Path(__file__).resolve().parent
DATABASE_PATH = str(BASE_DIR / "data" / "verilock.db")


CAMERA_INDEX = 0

CAMERA_WIDTH = 640

CAMERA_HEIGHT = 480

# Request 15 camera frames per second. Preview and face analysis use fewer frames,
# so a higher capture rate would mostly add unused frames.
CAMERA_FPS = 15
# Keep low-light capture at 15 fps to avoid excessive motion blur.
# Longer exposures can help brightness but make movement harder to capture.
CAMERA_LOW_LIGHT_MIN_FPS = 15
CAMERA_PREVIEW_INTERVAL = 0.08
# Space out face checks so the preview stays responsive.
FACE_ANALYSIS_INTERVAL = 0.18
FACE_ANALYSIS_COOLDOWN = 0.06


FACE_EMBEDDINGS_PATH = "data/face_embeddings"


# Use the smaller buffalo_s model to keep processing manageable on the Pi.
# InsightFace may download it the first time unless it is already installed.
FACE_MODEL_NAME = "buffalo_s"
FACE_DETECTION_SIZE = (640, 640)
# Leave a little space around the face box on screen.
# Keep detected faces away from the edge of the image.
FACE_BOX_PADDING = 0.18
FACE_FRAME_MARGIN = 0.10
# Set how much of the camera image can be used for face matching.
FACE_REGION_WIDTH_FRACTION = 1
FACE_REGION_HEIGHT_FRACTION = 1
# Ignore faces that are too small in the frame.
FACE_MIN_HEIGHT_FRACTION = 0.10
# Brighten dim images with CLAHE before checking faces.
# Apply the same adjustment when enrolling and recognizing someone.
FACE_LOW_LIGHT_ENHANCEMENT = True
# Ask for better lighting below this brightness level.
FACE_DARK_FRAME_THRESHOLD = 30
# Reject frames with too much blown-out detail.
FACE_OVEREXPOSED_PIXEL_FRACTION = 0.35
FACE_ENROLLMENT_SAMPLES = 10
REQUIRED_RECOGNITION_FRAMES = 5
# Look for five matches within the recent frames, not necessarily in a row.
RECOGNITION_FRAME_WINDOW = 15
# A face match requires a similarity score of at least 0.55.

FACE_SIMILARITY_THRESHOLD = 0.55


RFID_READER = "PN532"

RFID_INTERFACE = "I2C"


RFID_SCAN_DELAY = 0.10
ULTRA_SECURE_FACTOR_TIMEOUT = 30.0


RFID_CARD_TYPE = "MIFARE_CLASSIC"


# This is the standard MIFARE Classic key used for the demo.


# Use a different key and reprogram cards if this goes beyond a demo.


RFID_MIFARE_KEY = bytes([
    0xFF,
    0xFF,
    0xFF,
    0xFF,
    0xFF,
    0xFF
])


# Block 4 holds card data; it is not the sector trailer.

# Avoid the manufacturer block and sector trailer blocks:

# Block 0 contains manufacturer information and the card UID.
# Block 3 is a sector trailer.
# Block 7 is a sector trailer.
# Block 11 is a sector trailer.


RFID_DATA_BLOCK = 4


RFID_CARD_MAGIC = b"VLK"

RFID_CARD_VERSION = 1


DISPLAY_NAME = "ELECROW 5-inch USB Touchscreen"

DISPLAY_WIDTH = 800

DISPLAY_HEIGHT = 480

DISPLAY_VIDEO_INTERFACE = "HDMI"

# USB handles touch input through the OS; Tkinter receives normal touch events.

DISPLAY_TOUCH_INTERFACE = "USB"


SERVO_MODEL = "DS3235-270 35KG Servo"


SERVO_PWM_FREQUENCY = 50


SERVO_MIN_PULSE_US = 500

SERVO_MAX_PULSE_US = 2500


SERVO_MAX_ANGLE = 270.0


# The 24-tooth servo gear drives the 48-tooth deadbolt gear, giving a 2:1 ratio.


SERVO_GEAR_TEETH = 24

DEADBOLT_GEAR_TEETH = 48

GEAR_RATIO = (
    DEADBOLT_GEAR_TEETH
    /
    SERVO_GEAR_TEETH
)


# The deadbolt turns about 90 degrees between locked and unlocked.


DEADBOLT_REQUIRED_TRAVEL = 90.0


# At this gear ratio, the servo needs to turn 180 degrees.


SERVO_REQUIRED_TRAVEL = (
    DEADBOLT_REQUIRED_TRAVEL
    *
    GEAR_RATIO
)


# Check both endpoints and the direction on the actual mechanism.


SERVO_LOCK_ANGLE = SERVO_REQUIRED_TRAVEL

SERVO_UNLOCK_ANGLE = 0.0


# Give the servo time to finish moving, including in simulation.

SERVO_MOVE_TIME = 1.0

# Test the SER0057 rack servo with the mechanism disconnected first.
# The rack uses 0 degrees to disengage and 77 degrees to engage. Check that both positions clear the mechanism.
# Nominal rack-and-pinion dimensions, in millimeters:
RACK_SERVO_MODEL = "SER0057"
RACK_PINION_TEETH = 20
RACK_TEETH = 16
RACK_LENGTH_MM = 76.2
RACK_GEAR_MODULE_MM = 1.5
RACK_PRESSURE_ANGLE_DEG = 20.0
RACK_REQUIRED_TRAVEL_MM = 15.0
RACK_PINION_PITCH_DIAMETER_MM = RACK_GEAR_MODULE_MM * RACK_PINION_TEETH
RACK_PINION_OUTSIDE_DIAMETER_MM = RACK_GEAR_MODULE_MM * (RACK_PINION_TEETH + 2)
# Rack travel equals pitch radius times rotation in radians.
# For 15 mm of travel on a 30 mm pitch diameter, the turn is about 57.3 degrees.
from math import pi
RACK_REQUIRED_ROTATION_DEG = (
    360.0 * RACK_REQUIRED_TRAVEL_MM / (pi * RACK_PINION_PITCH_DIAMETER_MM)
)
RACK_SERVO_CALIBRATED = True
RACK_SERVO_PWM_FREQUENCY = 50
RACK_SERVO_MIN_PULSE_US = 1000
RACK_SERVO_MAX_PULSE_US = 2000
RACK_SERVO_MAX_ANGLE = 180.0
RACK_SERVO_DISENGAGED_ANGLE = 0.0
RACK_SERVO_ENGAGED_ANGLE = 77.0
RACK_SERVO_MOVE_TIME = 0.75
RACK_SERVO_RELEASE_TIME = 0.75

# Wait this many seconds before relocking after a successful scan, when autolock is on.


UNLOCK_DURATION = 60.0


ACCESS_GRANTED_DISPLAY_TIME = 2.0

ACCESS_DENIED_DISPLAY_TIME = 2.0


if SERVO_LOCK_ANGLE < 0:

    raise ValueError(
        "SERVO_LOCK_ANGLE cannot be negative."
    )

if SERVO_UNLOCK_ANGLE > SERVO_MAX_ANGLE:

    raise ValueError(
        "SERVO_UNLOCK_ANGLE exceeds "
        "SERVO_MAX_ANGLE."
    )

if SERVO_REQUIRED_TRAVEL <= 0:

    raise ValueError(
        "SERVO_REQUIRED_TRAVEL must be "
        "greater than zero."
    )

if SERVO_MOVE_TIME < 0:

    raise ValueError(
        "SERVO_MOVE_TIME cannot be negative."
    )


if not 0 <= SERVO_LOCK_ANGLE <= SERVO_MAX_ANGLE or not 0 <= SERVO_UNLOCK_ANGLE <= SERVO_MAX_ANGLE:
    raise ValueError("Main servo endpoints outside calibrated range")
if RACK_PINION_TEETH <= 0 or RACK_TEETH <= 0 or RACK_GEAR_MODULE_MM <= 0:
    raise ValueError("Rack-and-pinion gear geometry must be positive")
if RACK_LENGTH_MM <= 0 or RACK_REQUIRED_TRAVEL_MM <= 0:
    raise ValueError("Rack length and required travel must be positive")
if RACK_REQUIRED_TRAVEL_MM > RACK_LENGTH_MM:
    raise ValueError("Required rack travel exceeds nominal rack length")
if not 0 < RACK_PRESSURE_ANGLE_DEG < 90:
    raise ValueError("Invalid rack-and-pinion pressure angle")
if RACK_REQUIRED_ROTATION_DEG > RACK_SERVO_MAX_ANGLE:
    raise ValueError("Required rack servo rotation exceeds configured range")
if RACK_SERVO_GPIO == SERVO_GPIO:
    raise ValueError("Rack and deadbolt servos must use different GPIOs")
if not 0 <= RACK_SERVO_DISENGAGED_ANGLE <= RACK_SERVO_MAX_ANGLE:
    raise ValueError("Invalid rack disengaged endpoint")
if not 0 <= RACK_SERVO_ENGAGED_ANGLE <= RACK_SERVO_MAX_ANGLE:
    raise ValueError("Invalid rack engaged endpoint")
if RACK_SERVO_MOVE_TIME < 0 or RACK_SERVO_RELEASE_TIME < 0:
    raise ValueError("Rack movement delays must be nonnegative")

if not 0 < CAMERA_LOW_LIGHT_MIN_FPS <= CAMERA_FPS:
    raise ValueError("CAMERA_LOW_LIGHT_MIN_FPS must be positive and at most CAMERA_FPS.")

if not 0 <= FACE_DARK_FRAME_THRESHOLD <= 255:
    raise ValueError("FACE_DARK_FRAME_THRESHOLD must be between 0 and 255.")

if not 0 <= FACE_OVEREXPOSED_PIXEL_FRACTION <= 1:
    raise ValueError("FACE_OVEREXPOSED_PIXEL_FRACTION must be between 0 and 1.")

if not 0.0 <= FACE_BOX_PADDING <= 0.5:
    raise ValueError("FACE_BOX_PADDING must be between 0 and 0.5.")

if not 0.0 <= FACE_FRAME_MARGIN <= 0.5:
    raise ValueError("FACE_FRAME_MARGIN must be between 0 and 0.5.")

if not 0.0 < FACE_REGION_WIDTH_FRACTION <= 1.0:
    raise ValueError("FACE_REGION_WIDTH_FRACTION must be greater than 0 and at most 1.")

if not 0.0 < FACE_REGION_HEIGHT_FRACTION <= 1.0:
    raise ValueError("FACE_REGION_HEIGHT_FRACTION must be greater than 0 and at most 1.")

if not 0.0 <= FACE_MIN_HEIGHT_FRACTION < 1.0:
    raise ValueError("FACE_MIN_HEIGHT_FRACTION must be at least 0 and less than 1.")

if FACE_ENROLLMENT_SAMPLES < 1:
    raise ValueError("FACE_ENROLLMENT_SAMPLES must be at least 1.")

if REQUIRED_RECOGNITION_FRAMES < 1:

    raise ValueError(
        "REQUIRED_RECOGNITION_FRAMES must "
        "be at least 1."
    )

if RECOGNITION_FRAME_WINDOW < REQUIRED_RECOGNITION_FRAMES:
    raise ValueError(
        "RECOGNITION_FRAME_WINDOW must be at least REQUIRED_RECOGNITION_FRAMES."
    )

if not -1.0 <= FACE_SIMILARITY_THRESHOLD <= 1.0:

    raise ValueError(
        "FACE_SIMILARITY_THRESHOLD must be between -1 and 1."
    )


RFID_RESERVED_BLOCKS = (
    0,
    3,
    7,
    11,
    15,
    19,
    23,
    27,
    31,
    35,
    39,
    43,
    47,
    51,
    55,
    59,
    63
)

if RFID_DATA_BLOCK in RFID_RESERVED_BLOCKS:

    raise ValueError(
        "RFID_DATA_BLOCK cannot use a "
        "manufacturer or sector-trailer block."
    )

if len(RFID_MIFARE_KEY) != 6:

    raise ValueError(
        "RFID_MIFARE_KEY must contain "
        "exactly 6 bytes."
    )

if len(RFID_CARD_MAGIC) != 3:

    raise ValueError(
        "RFID_CARD_MAGIC must contain "
        "exactly 3 bytes."
    )


if DISPLAY_WIDTH <= 0:

    raise ValueError(
        "DISPLAY_WIDTH must be greater than zero."
    )

if DISPLAY_HEIGHT <= 0:

    raise ValueError(
        "DISPLAY_HEIGHT must be greater than zero."
    )

if DISPLAY_MONITOR_INDEX < 0:
    raise ValueError("DISPLAY_MONITOR_INDEX cannot be negative.")

# The ELECROW screen uses HDMI for video and USB for touch and power.
# No extra SPI or GPIO wiring is needed for touch.
