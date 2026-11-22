"""Check that VeriLock selects and uses the intended display."""

import ast
from pathlib import Path
import unittest
from unittest.mock import Mock, patch

from monitor_layout import (
    MonitorBounds,
    geometry_for_monitor,
    parse_wlr_randr_outputs,
    parse_xrandr_monitors,
    place_window_on_monitor,
    update_labwc_config,
    create_popup,
    show_messagebox,
)


class MonitorLayoutTests(unittest.TestCase):
    def test_parses_active_xrandr_monitor_order_and_geometry(self):
        monitors = parse_xrandr_monitors(
            """Monitors: 2
 0: +*HDMI-1 1920/509x1080/286+0+0 HDMI-1
 1: +HDMI-2 1280/344x720/194+1920+0 HDMI-2
"""
        )

        self.assertEqual(monitors[0], MonitorBounds(1920, 1080, 0, 0, "HDMI-1"))
        self.assertEqual(monitors[1], MonitorBounds(1280, 720, 1920, 0, "HDMI-2"))

    def test_parses_only_enabled_wayland_outputs(self):
        outputs = parse_wlr_randr_outputs(
            """HDMI-A-1 \"Primary panel\" (enabled)
  Enabled: yes
  Position: 0,0
DP-1 \"Disconnected panel\" (disabled)
  Enabled: no
HDMI-A-2 \"VeriLock panel\" (enabled)
  Enabled: yes
  Position: 1920,0
"""
        )

        self.assertEqual(outputs, ["HDMI-A-1", "HDMI-A-2"])

    def test_pi_output_order_selects_touchscreen_before_dell(self):
        outputs = parse_wlr_randr_outputs(
            'HDMI-A-1 "MPI5001"\n  Enabled: yes\n  Position: 1920,0\n'
            'HDMI-A-2 "DELL U2414H"\n  Enabled: yes\n  Position: 0,0\n'
        )
        self.assertEqual(outputs[0], "HDMI-A-1")

    def test_routing_preserves_touch_and_popup_fullscreen_rules(self):
        source = """<labwc_config>
          <touch deviceName="QDtech MPI5001 (USB 3-1)" mapToOutput="HDMI-A-1"/>
          <windowRules>
            <windowRule identifier="VeriLock">
              <action name="MoveToOutput" output="HDMI-A-2"/>
              <action name="ToggleFullscreen"/>
            </windowRule>
            <windowRule title="Administrator RFID Verification">
              <action name="MoveToOutput" output="HDMI-A-1"/>
            </windowRule>
          </windowRules>
        </labwc_config>"""
        result = update_labwc_config(source, "HDMI-A-1")
        import xml.etree.ElementTree as ET
        root = ET.fromstring(result)
        self.assertEqual(root.find("touch").get("mapToOutput"), "HDMI-A-1")
        rules = root.find("windowRules")
        self.assertEqual(len(rules), 4)
        main_rule = rules.find("windowRule")
        self.assertEqual(main_rule.find("action").get("output"), "HDMI-A-1")
        self.assertIsNotNone(main_rule.find("action[@name='ToggleFullscreen']"))
        popup = rules.find("windowRule[@title='Administrator RFID Verification']")
        self.assertEqual(popup.find("action").get("output"), "HDMI-A-1")
        self.assertEqual(update_labwc_config(result, "HDMI-A-1"), result)

    def test_centers_window_on_selected_x11_monitor(self):
        geometry = geometry_for_monitor(
            MonitorBounds(1280, 720, 1920, 0, "HDMI-2"),
            800,
            480,
            screen_width=3200,
            screen_height=720,
        )

        self.assertEqual(geometry, "800x480+2160+120")

    def test_converts_negative_monitor_origin_to_tk_offset(self):
        geometry = geometry_for_monitor(
            MonitorBounds(1920, 1080, -1920, 0, "HDMI-2"),
            800,
            480,
            screen_width=1920,
            screen_height=1080,
        )

        self.assertEqual(geometry, "800x480-2480+300")

    def test_rejects_window_larger_than_selected_monitor(self):
        with self.assertRaisesRegex(RuntimeError, "does not fit"):
            geometry_for_monitor(
                MonitorBounds(800, 480, 0, 0, "HDMI-2"),
                801,
                480,
                screen_width=800,
                screen_height=480,
            )

    def test_labwc_rule_is_added_and_updated_without_duplicates(self):
        source = "<labwc_config><theme /></labwc_config>"

        first = update_labwc_config(source, "HDMI-A-2")
        repeated = update_labwc_config(first, "HDMI-A-2")
        changed = update_labwc_config(first, "HDMI-A-1")

        self.assertIn('identifier="VeriLock"', first)
        self.assertIn('output="HDMI-A-2"', first)
        self.assertEqual(first, repeated)
        self.assertIn('output="HDMI-A-1"', changed)
        self.assertEqual(changed.count('name="MoveToOutput"'), 3)

    def test_dialogs_share_output_without_inheriting_fullscreen(self):
        import xml.etree.ElementTree as ET
        source = '''<labwc_config><windowRules>
          <windowRule identifier="VeriLock">
            <action name="ToggleFullscreen"/>
          </windowRule>
        </windowRules></labwc_config>'''
        result = update_labwc_config(source, "HDMI-A-1")
        changed = update_labwc_config(result, "HDMI-A-2")
        rules = ET.fromstring(changed).find("windowRules")
        for selector in ("[@identifier='VeriLockDialog']", "[@title='VeriLock - *']"):
            rule = rules.find("windowRule" + selector)
            self.assertIsNotNone(rule)
            self.assertEqual(rule.find("action[@name='MoveToOutput']").get("output"),
                             "HDMI-A-2")
            self.assertIsNone(rule.find("action[@name='ToggleFullscreen']"))
        self.assertIsNotNone(rules.find(
            "windowRule[@identifier='VeriLock']/action[@name='ToggleFullscreen']"))
        self.assertEqual(changed, update_labwc_config(changed, "HDMI-A-2"))

    def test_popup_class_and_parent_are_set_before_mapping(self):
        parent = Mock()
        with patch("tkinter.Toplevel") as constructor:
            window = create_popup(parent, bg="black")
        constructor.assert_called_once_with(parent, bg="black", class_="VeriLockDialog")
        window.transient.assert_called_once_with(parent.winfo_toplevel())

    def test_messageboxes_use_parent_and_return_confirmation(self):
        parent = Mock()
        with patch("tkinter.messagebox.askyesno", return_value=False) as show:
            result = show_messagebox(parent, "askyesno", "Delete User", "Confirm?")
        self.assertFalse(result)
        show.assert_called_once_with("VeriLock - Delete User", "Confirm?", parent=parent)

    def test_messagebox_keeps_explicit_dialog_parent(self):
        owner, dialog = Mock(), Mock()
        with patch("tkinter.messagebox.showinfo") as show:
            show_messagebox(owner, "showinfo", "Saved", "Done", parent=dialog)
        show.assert_called_once_with("VeriLock - Saved", "Done", parent=dialog)

    def test_admin_uses_shared_helpers_for_every_popup_and_messagebox(self):
        tree = ast.parse(Path(__file__).with_name("admin.py").read_text())
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
                continue
            if isinstance(node.func.value, ast.Name):
                self.assertNotEqual(node.func.value.id, "messagebox")
                self.assertFalse(node.func.value.id == "tk" and node.func.attr == "Toplevel")

    def test_main_and_admin_set_verilock_class_at_root_construction(self):
        # Inspect startup without initializing cameras, GPIO, or a desktop.
        for filename in ("main.py", "admin.py"):
            with self.subTest(module=filename):
                tree = ast.parse(Path(__file__).with_name(filename).read_text())
                constructors = [
                    node for node in ast.walk(tree)
                    if isinstance(node, ast.Call)
                    and isinstance(node.func, ast.Attribute)
                    and isinstance(node.func.value, ast.Name)
                    and node.func.value.id == "tk"
                    and node.func.attr == "Tk"
                ]
                self.assertTrue(constructors, "No Tk root constructor found")
                for constructor in constructors:
                    keywords = {item.arg: item.value for item in constructor.keywords}
                    self.assertIn("className", keywords)
                    self.assertEqual(ast.literal_eval(keywords["className"]), "VeriLock")

    def test_places_production_window_using_active_monitor_geometry(self):
        class FakeWindow:
            def __init__(self):
                self.geometry_value = None
                self.class_name = "VeriLock"

            def wm_class(self, value):
                raise AssertionError("Tk class must be set at root construction")

            def geometry(self, value):
                self.geometry_value = value

            def winfo_screenwidth(self):
                return 3200

            def winfo_screenheight(self):
                return 720

        window = FakeWindow()
        with (
            patch("monitor_layout.config.SIMULATION_MODE", False),
            patch("monitor_layout.config.DISPLAY_MONITOR_INDEX", 1),
            patch("monitor_layout.config.FULLSCREEN", False),
            patch("monitor_layout._wayland_environment", return_value=None),
            patch(
                "monitor_layout._x11_monitor_for_index",
                return_value=MonitorBounds(1280, 720, 1920, 0, "HDMI-2"),
            ),
        ):
            place_window_on_monitor(window, 800, 480)

        self.assertEqual(window.class_name, "VeriLock")
        self.assertEqual(window.geometry_value, "800x480+2160+120")


if __name__ == "__main__":
    unittest.main()
