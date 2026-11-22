"""Place VeriLock windows on the selected monitor."""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import tempfile
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path

import config


@dataclass(frozen=True)
class MonitorBounds:
    """Store the size and desktop position of an active display."""

    width: int
    height: int
    x: int
    y: int
    name: str = ""


_XRANDR_MONITOR = re.compile(
    r"^\s*(?P<index>\d+):\s+\S+\s+"
    r"(?P<width>\d+)(?:/\d+)?x(?P<height>\d+)(?:/\d+)?"
    r"(?P<x>[+-]\d+)(?P<y>[+-]\d+)(?:\s|$)"
)
_WAYLAND_ROUTE_OUTPUT: str | None = None


def create_popup(parent, **options):
    """Create a VeriLock dialog that stays above its parent window."""
    import tkinter as tk

    options["class_"] = "VeriLockDialog"
    window = tk.Toplevel(parent, **options)
    window.transient(parent.winfo_toplevel())
    return window


def show_messagebox(owner, method, title, message, **options):
    """Give native message boxes a parent and a shared display-routing title."""
    from tkinter import messagebox

    options.setdefault("parent", owner)
    title = f"VeriLock - {title}"
    return getattr(messagebox, method)(title, message, **options)


def parse_xrandr_monitors(output: str) -> dict[int, MonitorBounds]:
    """Read active display sizes and positions from xrandr output."""
    monitors = {}
    for line in output.splitlines():
        match = _XRANDR_MONITOR.match(line)
        if match is None:
            continue

        values = match.groupdict()
        index = int(values["index"])
        monitors[index] = MonitorBounds(
            width=int(values["width"]),
            height=int(values["height"]),
            x=int(values["x"]),
            y=int(values["y"]),
            name=line.split()[-1],
        )
    return monitors


def parse_wlr_randr_outputs(output: str) -> list[str]:
    """List enabled Wayland outputs in the order returned by wlr-randr."""
    outputs = []
    current_name = None
    current_enabled = False

    def finish_output():
        if current_name is not None and current_enabled:
            outputs.append(current_name)

    for line in output.splitlines():
        if not line.strip():
            continue
        if not line[0].isspace():
            finish_output()
            current_name = line.split(None, 1)[0]
            header_status = re.search(r"\((enabled|disabled)\)", line, re.I)
            current_enabled = bool(
                header_status and header_status.group(1).lower() == "enabled"
            )
            continue

        if current_name is not None:
            enabled_status = re.match(r"\s*Enabled:\s*(yes|no)\s*$", line, re.I)
            if enabled_status:
                current_enabled = enabled_status.group(1).lower() == "yes"

    finish_output()
    return outputs


def geometry_for_monitor(
    bounds: MonitorBounds,
    width: int,
    height: int,
    screen_width: int,
    screen_height: int,
) -> str:
    """Build a Tkinter window position centered on the selected X11 display."""
    if width <= 0 or height <= 0:
        raise ValueError("Window dimensions must be positive.")
    if width > bounds.width or height > bounds.height:
        raise RuntimeError(
            f"VeriLock window ({width}x{height}) does not fit on "
            f"monitor {bounds.name!r} ({bounds.width}x{bounds.height})."
        )

    x = bounds.x + (bounds.width - width) // 2
    y = bounds.y + (bounds.height - height) // 2
    x_offset = f"+{x}" if x >= 0 else f"-{screen_width - x - width}"
    y_offset = f"+{y}" if y >= 0 else f"-{screen_height - y - height}"
    return f"{width}x{height}{x_offset}{y_offset}"


def update_labwc_config(xml_text: str, output_name: str) -> str:
    """Keep the existing desktop settings and add VeriLock’s display rule."""
    parser = ET.XMLParser(target=ET.TreeBuilder(insert_comments=True))
    root = ET.fromstring(xml_text, parser=parser)

    namespace = ""
    local_name = root.tag
    if root.tag.startswith("{"):
        namespace, local_name = root.tag[1:].split("}", 1)
        ET.register_namespace("", namespace)

    if local_name not in ("labwc_config", "openbox_config"):
        raise ValueError(f"Unsupported labwc config root: {root.tag}")

    def tag(name):
        return f"{{{namespace}}}{name}" if namespace else name

    rules = root.find(tag("windowRules"))
    if rules is None:
        rules = ET.SubElement(root, tag("windowRules"))

    route_rule = None
    for rule in rules.findall(tag("windowRule")):
        if rule.attrib.get("identifier", "").casefold() != "verilock":
            continue
        extra = set(rule.attrib) - {"identifier", "matchOnce"}
        if not extra and rule.attrib.get("matchOnce", "false").lower() != "true":
            route_rule = rule
            break

    if route_rule is None:
        route_rule = ET.SubElement(
            rules, tag("windowRule"), {"identifier": "VeriLock"}
        )

    actions = [
        action for action in route_rule.findall(tag("action"))
        if action.attrib.get("name", "").casefold() == "movetooutput"
    ]
    if actions:
        actions[0].set("output", output_name)
        for duplicate in actions[1:]:
            route_rule.remove(duplicate)
    else:
        ET.SubElement(
            route_rule, tag("action"),
            {"name": "MoveToOutput", "output": output_name}
        )

    # Custom popups have their own class so the main-window fullscreen rule
    # does not expand them. Native Tk message boxes use a shared title prefix.
    for criteria in (
        {"identifier": "VeriLockDialog"},
        {"title": "VeriLock - *"},
    ):
        dialog_rule = next(
            (rule for rule in rules.findall(tag("windowRule"))
             if rule.attrib == criteria),
            None,
        )
        if dialog_rule is None:
            dialog_rule = ET.SubElement(rules, tag("windowRule"), criteria)
        moves = [action for action in dialog_rule.findall(tag("action"))
                 if action.get("name", "").casefold() == "movetooutput"]
        if moves:
            moves[0].set("output", output_name)
            for duplicate in moves[1:]:
                dialog_rule.remove(duplicate)
        else:
            ET.SubElement(dialog_rule, tag("action"),
                          {"name": "MoveToOutput", "output": output_name})

    ET.indent(root, space="  ")
    return ET.tostring(root, encoding="unicode") + "\n"


def _wayland_environment(source: dict[str, str] | None = None):
    """Find an available Wayland socket and include it in the environment."""
    source = dict(os.environ if source is None else source)
    runtime_dir = Path(source.get("XDG_RUNTIME_DIR", f"/run/user/{os.getuid()}"))
    display = source.get("WAYLAND_DISPLAY")

    if display:
        display_path = Path(display)
        if not display_path.is_absolute():
            display_path = runtime_dir / display_path
        if display_path.exists():
            source["WAYLAND_DISPLAY"] = display_path.name
            source["XDG_RUNTIME_DIR"] = str(display_path.parent)
            return source

    if runtime_dir.is_dir():
        sockets = sorted(runtime_dir.glob("wayland-*"))
        if sockets:
            source["XDG_RUNTIME_DIR"] = str(runtime_dir)
            source["WAYLAND_DISPLAY"] = sockets[0].name
            return source

    if source.get("XDG_SESSION_TYPE", "").lower() == "wayland":
        return source
    return None


def _labwc_config_path(environment: dict[str, str]) -> Path:
    home = Path(environment.get("HOME", str(Path.home())))
    config_home = Path(environment.get("XDG_CONFIG_HOME", home / ".config"))
    return config_home / "labwc" / "rc.xml"


def _initial_labwc_config(environment: dict[str, str]) -> str:
    for directory in environment.get("XDG_CONFIG_DIRS", "/etc/xdg").split(":"):
        candidate = Path(directory) / "labwc" / "rc.xml"
        if candidate.is_file():
            return candidate.read_text(encoding="utf-8")
    return "<labwc_config />\n"


def _write_labwc_config(path: Path, contents: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    previous_mode = path.stat().st_mode & 0o777 if path.exists() else 0o644
    with tempfile.NamedTemporaryFile(
        mode="w",
        encoding="utf-8",
        dir=path.parent,
        prefix=f".{path.name}.",
        suffix=".tmp",
        delete=False,
    ) as temporary:
        temporary.write(contents)
        temporary_path = Path(temporary.name)
    os.chmod(temporary_path, previous_mode)
    os.replace(temporary_path, path)


def _configure_labwc_output(output_name: str, environment: dict[str, str]) -> None:
    config_path = _labwc_config_path(environment)
    if config_path.exists():
        original = config_path.read_text(encoding="utf-8")
    else:
        original = _initial_labwc_config(environment)

    updated = update_labwc_config(original, output_name)
    if updated == original:
        return

    if config_path.exists():
        backup = config_path.with_name(config_path.name + ".before-verilock")
        if not backup.exists():
            shutil.copy2(config_path, backup)

    _write_labwc_config(config_path, updated)
    try:
        result = subprocess.run(
            ["labwc", "--reconfigure"],
            check=False,
            capture_output=True,
            text=True,
            timeout=10,
            env=environment,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        raise RuntimeError(
            "VeriLock saved its labwc monitor rule but could not reload labwc. "
            "Run `labwc --reconfigure` in the Pi desktop session."
        ) from error

    if result.returncode != 0:
        detail = result.stderr.strip() or result.stdout.strip()
        raise RuntimeError(
            "VeriLock saved its labwc monitor rule but labwc did not reload it"
            + (f": {detail}" if detail else ".")
        )


def _wayland_output_for_index(index: int, environment: dict[str, str]) -> str:
    try:
        result = subprocess.run(
            ["wlr-randr"],
            check=True,
            capture_output=True,
            text=True,
            timeout=5,
            env=environment,
        )
    except (OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired) as error:
        raise RuntimeError(
            "Could not list active Wayland monitors with wlr-randr."
        ) from error

    outputs = parse_wlr_randr_outputs(result.stdout)
    if index >= len(outputs):
        raise RuntimeError(
            f"VeriLock is configured for monitor {index}, but only "
            f"{len(outputs)} active Wayland monitor(s) were found."
        )
    return outputs[index]


def _x11_monitor_for_index(index: int) -> MonitorBounds:
    try:
        result = subprocess.run(
            ["xrandr", "--listactivemonitors"],
            check=True,
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired) as error:
        raise RuntimeError(
            "Could not list active X11 monitors with xrandr."
        ) from error

    monitors = parse_xrandr_monitors(result.stdout)
    if index not in monitors:
        raise RuntimeError(
            f"VeriLock is configured for monitor {index}, but only "
            f"{len(monitors)} active X11 monitor(s) were found."
        )
    return monitors[index]


def place_window_on_monitor(window, width: int, height: int) -> None:
    """Place a VeriLock window on the selected monitor when using hardware mode."""
    if config.SIMULATION_MODE:
        return

    index = config.DISPLAY_MONITOR_INDEX
    if index < 0:
        raise ValueError("DISPLAY_MONITOR_INDEX cannot be negative.")
    if config.FULLSCREEN:
        raise RuntimeError(
            "Monitor-specific routing requires FULLSCREEN=False so the "
            "window manager can place VeriLock on the selected monitor."
        )

    wayland = _wayland_environment()
    if wayland is not None:
        global _WAYLAND_ROUTE_OUTPUT
        if _WAYLAND_ROUTE_OUTPUT is None:
            _WAYLAND_ROUTE_OUTPUT = _wayland_output_for_index(index, wayland)
            _configure_labwc_output(_WAYLAND_ROUTE_OUTPUT, wayland)
        window.geometry(f"{width}x{height}")
        return

    monitor = _x11_monitor_for_index(index)
    window.geometry(
        geometry_for_monitor(
            monitor,
            width,
            height,
            window.winfo_screenwidth(),
            window.winfo_screenheight(),
        )
    )
