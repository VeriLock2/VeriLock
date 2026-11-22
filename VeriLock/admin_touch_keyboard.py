"""Show a touch keyboard above the admin dialog containing the active field."""
import tkinter as tk
from tkinter import ttk


def compact_layout(root_bounds, owner_size):
    """Keep the small keyboard on the admin display, beside the form if it fits."""
    rx, ry, rw, rh = root_bounds
    ow, oh = owner_size
    width, height = min(350, rw), min(190, rh)
    if ow + width + 24 <= rw:
        owner = f"{ow}x{oh}{rx + 8:+d}{ry + max(0, (rh - oh) // 2):+d}"
        x = rx + 8 + ow + 8
    else:
        owner = None
        x = rx + max(0, (rw - width) // 2)
    y = ry + max(0, (rh - height) // 2) if owner else ry + max(0, rh - height - 28)
    return owner, f"{width}x{height}{x:+d}{y:+d}"


class AdminTouchKeyboard:
    def __init__(self, root):
        self.root = root
        self.target = None
        self.dismissed_target = None
        self.window = None
        self.shift = False
        self.owner_restore = None
        root.bind_all("<FocusIn>", self._focus, add="+")
        root.bind_all("<Button-1>", self._tap, add="+")
        root.bind_all("<Map>", self._mapped, add="+")
        root.bind_all("<Destroy>", self._destroyed, add="+")

    def _editable(self, widget):
        return (isinstance(widget, (tk.Entry, ttk.Entry))
                and str(widget.cget("state")) not in ("disabled", "readonly"))

    def _focus(self, event):
        if self._editable(event.widget) and event.widget is not self.dismissed_target:
            self.open_for(event.widget)

    def _tap(self, event):
        if self._editable(event.widget):
            self.dismissed_target = None
            self.open_for(event.widget)

    def _mapped(self, event):
        if self._editable(event.widget):
            title = event.widget.winfo_toplevel().title().casefold()
            if title in ("add user", "add verilock user", "enroll user", "enroll verilock user"):
                self.open_for(event.widget)

    def _destroyed(self, event):
        if event.widget is self.target:
            self.hide()

    def open_for(self, entry):
        self.target = entry
        self.root.after_idle(self.show)

    def show(self):
        try:
            if self.target is None or not self.target.winfo_exists():
                return
            parent = self.target.winfo_toplevel()
            if self.window is not None and self.window.winfo_exists():
                self.window.transient(parent)
                self.window.lift()
                return
            # Use a managed dialog so labwc can route and stack it above its owner.
            self.window = tk.Toplevel(parent, class_="VeriLockDialog")
            self.window.title("VeriLock - Touch Keyboard")
            self.window.transient(parent)
            self.window.configure(bg="#121C27")
            self.window.resizable(False, False)
            self.window.attributes("-topmost", True)
            self.window.protocol("WM_DELETE_WINDOW", self.hide)
            self.root.update_idletasks()
            owner_geometry, keyboard_geometry = compact_layout(
                (self.root.winfo_rootx(), self.root.winfo_rooty(),
                 max(1, self.root.winfo_width()), max(1, self.root.winfo_height())),
                (parent.winfo_width(), parent.winfo_height()),
            )
            if owner_geometry is not None:
                self.owner_restore = (parent, parent.geometry())
                parent.geometry(owner_geometry)
            self.window.geometry(keyboard_geometry)
            self.frame = tk.Frame(self.window, bg="#121C27")
            self.frame.pack(fill="both", expand=True, padx=3, pady=3)
            self._draw()
            self.window.lift()
            self.target.focus_set()
        except tk.TclError as error:
            print(f"[KEYBOARD] Could not open keyboard: {error}")
            self.hide()

    def _draw(self):
        for child in self.frame.winfo_children():
            child.destroy()
        for keys in ("1234567890", "qwertyuiop", "asdfghjkl", "zxcvbnm"):
            row = tk.Frame(self.frame, bg="#121C27")
            row.pack(fill="x", expand=True)
            for key in keys:
                label = key.upper() if self.shift else key
                self._button(row, label, lambda char=label: self._type(char))
        row = tk.Frame(self.frame, bg="#121C27")
        row.pack(fill="x", expand=True)
        for label, action in (("Shift", self._toggle_shift),
                              ("Space", lambda: self._type(" ")),
                              ("Backspace", self._backspace), ("Done", self.hide)):
            self._button(row, label, action)

    @staticmethod
    def _button(parent, label, action):
        tk.Button(parent, text=label, command=action, takefocus=False,
                  font=("Arial", 10, "bold"), bg="#4A6A8A", fg="white",
                  relief="flat").pack(side="left", fill="both", expand=True,
                                      padx=1, pady=1)

    def _type(self, char):
        try:
            if self.target is None or not self.target.winfo_exists():
                self.hide()
                return
            if not self._editable(self.target):
                return
            if self.target.selection_present():
                self.target.delete("sel.first", "sel.last")
            self.target.insert("insert", char)
        except tk.TclError:
            self.hide()

    def _backspace(self):
        try:
            if self.target is None or not self.target.winfo_exists():
                self.hide()
                return
            if not self._editable(self.target):
                return
            if self.target.selection_present():
                self.target.delete("sel.first", "sel.last")
            else:
                index = self.target.index("insert")
                if index > 0:
                    self.target.delete(index - 1, index)
        except tk.TclError:
            self.hide()

    def _toggle_shift(self):
        self.shift = not self.shift
        self._draw()

    def hide(self):
        window, self.window = self.window, None
        restore, self.owner_restore = self.owner_restore, None
        self.dismissed_target = self.target
        self.target = None
        if window is not None:
            try:
                window.destroy()
            except tk.TclError:
                pass
        if restore is not None:
            parent, geometry = restore
            try:
                if parent.winfo_exists():
                    parent.geometry(geometry)
            except tk.TclError:
                pass
