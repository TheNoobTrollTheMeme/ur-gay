"""Tkinter board and tray controls for the arm controller."""
import tkinter as tk
from tkinter import font as tkfont


BACKGROUND = "#101722"
PANEL = "#182332"
SURFACE = "#223246"
BORDER = "#344961"
TEXT = "#f2f6fb"
MUTED = "#aebed0"
BLUE = "#1f64a9"
BLUE_HOVER = "#287ac8"
SELECTED = "#b5e2ff"
DARK_TEXT = "#10263d"
RED = "#a43747"


class ArmBoardUI:
    """Display the physical board and deliver choices to a controller."""

    def __init__(self, root, on_square, on_stop, on_close=None):
        self.root = root
        self.on_square = on_square
        self.on_stop = on_stop
        self.busy = False
        self.grab = None
        self.target = None
        self.squares = {}
        self.tray_buttons = {}
        self.mode = tk.StringVar(root, value="place")
        self.live = tk.BooleanVar(root, value=False)
        self.status = tk.StringVar(root, value="Ready")
        self.tray_help = tk.StringVar(root)
        self.family = "Inter" if "Inter" in tkfont.families(root) else "DejaVu Sans"

        root.title("Arm Board")
        root.configure(bg=BACKGROUND)
        root.minsize(800, 570)
        root.columnconfigure(0, weight=1)
        root.rowconfigure(0, weight=1)
        if on_close is not None:
            root.protocol("WM_DELETE_WINDOW", on_close)
        root.bind("<Escape>", self._stop)

        window = tk.Frame(root, bg=BACKGROUND, padx=20, pady=16)
        window.grid(row=0, column=0, sticky="nsew")
        window.columnconfigure(0, weight=1)
        window.rowconfigure(1, weight=1)

        header = tk.Frame(window, bg=BACKGROUND)
        header.grid(row=0, column=0, sticky="ew", pady=(0, 14))
        header.columnconfigure(0, weight=1)
        self._label(header, "Arm Board", 24, bold=True, bg=BACKGROUND).grid(
            row=0, column=0, sticky="w",
        )
        self.live_button = tk.Checkbutton(
            header, text="Run on square click", variable=self.live, command=self._refresh,
            bg=SURFACE, fg=TEXT, selectcolor=PANEL, activebackground=SURFACE,
            activeforeground=TEXT, disabledforeground=MUTED,
            font=(self.family, 11, "bold"), padx=10, pady=7,
        )
        self.live_button.grid(row=0, column=1, padx=(12, 10), sticky="e")
        self._button(header, "Stop (Esc)", self.on_stop, bg=RED).grid(row=0, column=2, sticky="e")

        controls = tk.Frame(window, bg=BACKGROUND)
        controls.grid(row=1, column=0, sticky="nsew")
        controls.columnconfigure(0, weight=2, uniform="panels")
        controls.columnconfigure(1, weight=3, uniform="panels")
        controls.rowconfigure(0, weight=1)
        self._build_board(controls)
        self._build_trays(controls)

        activity = self._panel(window)
        activity.grid(row=2, column=0, sticky="ew", pady=(12, 0))
        activity.columnconfigure(0, weight=1)
        self._label(activity, "Activity", 12, bold=True).grid(row=0, column=0, sticky="w")
        self.log = tk.Text(
            activity, height=5, wrap="word", bg=BACKGROUND, fg=TEXT,
            font=(self.family, 10), padx=8, pady=6, relief="flat", state="disabled",
        )
        scrollbar = tk.Scrollbar(activity, command=self.log.yview)
        self.log.configure(yscrollcommand=scrollbar.set)
        self.log.grid(row=1, column=0, sticky="ew", pady=(6, 0))
        scrollbar.grid(row=1, column=1, sticky="ns", pady=(6, 0))

        footer = tk.Frame(window, bg=BACKGROUND)
        footer.grid(row=3, column=0, sticky="ew", pady=(10, 0))
        footer.columnconfigure(0, weight=1)
        self._label(footer, variable=self.status, bg=BACKGROUND).grid(row=0, column=0, sticky="w")
        self.connection_label = self._label(footer, size=10, bold=True, bg=BACKGROUND)
        self.connection_label.grid(row=0, column=1, sticky="e")

        self._refresh()

    def _build_board(self, parent):
        panel = self._panel(parent)
        panel.grid(row=0, column=0, sticky="nsew", padx=(0, 10))
        panel.columnconfigure(0, weight=1)
        panel.rowconfigure(1, weight=1)
        modes = tk.Frame(panel, bg=PANEL)
        modes.grid(row=0, column=0, sticky="ew", pady=(0, 8))
        self._label(modes, "Board", 14, bold=True).pack(side="left", padx=(0, 8))
        self.mode_buttons = []
        for name, value in (("Place", "place"), ("Attack", "attack")):
            button = tk.Radiobutton(
                modes, text=name, variable=self.mode, value=value, command=self._refresh,
                indicatoron=False, bg=SURFACE, selectcolor=BLUE, fg=TEXT,
                activebackground=BLUE_HOVER, activeforeground=TEXT, disabledforeground=MUTED,
                font=(self.family, 10, "bold"), padx=8, pady=3, relief="flat",
            )
            button.pack(side="left", padx=(2, 0))
            self.mode_buttons.append(button)

        board = tk.Frame(panel, bg=PANEL)
        board.grid(row=1, column=0, sticky="nsew")
        for index in range(3):
            board.columnconfigure(index, weight=1, uniform="column")
            board.rowconfigure(index, weight=1, uniform="row")
        for row in range(1, 4):
            for col in range(1, 4):
                button = self._button(
                    board, f"C{col}R{row}", lambda c=col, r=row: self._choose_square(c, r),
                    size=13,
                )
                button.grid(row=row - 1, column=col - 1, sticky="nsew", padx=3, pady=3)
                self.squares[col, row] = button

    def _build_trays(self, parent):
        panel = self._panel(parent)
        panel.grid(row=0, column=1, sticky="nsew")
        panel.columnconfigure(0, weight=1)
        panel.rowconfigure(2, weight=1)
        heading = tk.Frame(panel, bg=PANEL)
        heading.grid(row=0, column=0, sticky="ew")
        self._label(heading, "Trays", 14, bold=True).pack(side="left")
        self.clear_button = self._button(heading, "Clear pickup", self._clear_grab, size=9, pady=4)
        self.clear_button.pack(side="right")
        self._label(
            panel, variable=self.tray_help, size=10, color=MUTED, anchor="w",
        ).grid(row=1, column=0, sticky="ew", pady=(3, 8))

        trays = tk.Frame(panel, bg=PANEL)
        trays.grid(row=2, column=0, sticky="nsew")
        trays.rowconfigure(0, weight=1)
        layouts = (
            ("Left", ((1, "Cylinder", 2, 0), (2, "Cube", 1, 1))),
            ("Center", ((3, "Trophy", 1, 0), (4, "Cube", 1, 1))),
            ("Right", ((5, "Cylinder", 1, 0), (6, "Trophy", 2, 1))),
        )
        for column, (name, slots) in enumerate(layouts):
            trays.columnconfigure(column, weight=1, uniform="tray")
            tray = tk.Frame(trays, bg=BLUE, padx=4, pady=5)
            tray.grid(row=0, column=column, sticky="nsew", padx=3)
            for index in (0, 1):
                tray.columnconfigure(index, weight=1, uniform="slot")
                tray.rowconfigure(index + 1, weight=1, uniform="depth")
            self._label(tray, name, 10, bold=True, bg=BLUE).grid(
                row=0, column=0, columnspan=2, pady=(0, 5),
            )
            for grab, shape, row, position in slots:
                button = self._button(
                    tray, f"{shape}\nGrab {grab}", lambda g=grab: self._choose_grab(g),
                    size=9, padx=3,
                )
                button.grid(row=row, column=position, sticky="nsew", padx=2, pady=2)
                self.tray_buttons[grab] = button

    def _panel(self, parent):
        return tk.Frame(
            parent, bg=PANEL, padx=12, pady=10,
            highlightbackground=BORDER, highlightthickness=1,
        )

    def _label(
        self, parent, text="", size=11, *, bold=False, bg=PANEL,
        color=TEXT, variable=None, anchor="center",
    ):
        return tk.Label(
            parent, text=text, textvariable=variable, bg=bg, fg=color, anchor=anchor,
            font=(self.family, size, "bold" if bold else "normal"),
        )

    def _button(self, parent, text, action, *, bg=SURFACE, size=11, padx=8, pady=6):
        return tk.Button(
            parent, text=text, command=action, bg=bg, fg=TEXT, relief="flat",
            activebackground=BLUE_HOVER, activeforeground=TEXT, disabledforeground=MUTED,
            font=(self.family, size, "bold"), padx=padx, pady=pady,
        )

    def _choose_grab(self, grab):
        self.grab = None if self.grab == grab else grab
        self._refresh()

    def _clear_grab(self):
        self.grab = None
        self._refresh()

    def _choose_square(self, col, row):
        if self.busy:
            return
        self.target = (col, row)
        self._refresh()
        mode = self.mode.get()
        grab = self.grab
        if mode == "attack" and ((col, row) != (2, 2) or grab in (3, 6)):
            grab = None
        self.on_square(col, row, mode, grab, self.live.get())

    def _stop(self, _event):
        self.on_stop()
        return "break"

    def _refresh(self):
        place = self.mode.get() == "place"
        live = self.live.get()
        state = "disabled" if self.busy else "normal"
        self.live_button.configure(state=state)
        self.connection_label.configure(
            text="Live · Click a square to run" if live else "Preview · Click a square to inspect",
            fg="#ffc285" if live else MUTED,
        )
        for button in self.mode_buttons:
            button.configure(state=state)
        for square, button in self.squares.items():
            button.configure(state=state, bg=BLUE if square == self.target else SURFACE)

        self.clear_button.configure(state="normal" if not self.busy and self.grab else "disabled")
        for grab, button in self.tray_buttons.items():
            can_pick = place or grab not in (3, 6)
            selected = can_pick and grab == self.grab
            button.configure(
                state="normal" if can_pick and not self.busy else "disabled",
                bg=SELECTED if selected else SURFACE, fg=DARK_TEXT if selected else TEXT,
            )
        if not place:
            if self.grab in (1, 2, 4, 5):
                self.tray_help.set(f"Grab {self.grab} selected for C2R2.")
            else:
                self.tray_help.set("Choose a cylinder or cube for C2R2 if needed.")
        elif self.grab is None:
            self.tray_help.set("Optional pickup before move and place.")
        else:
            self.tray_help.set(f"Grab {self.grab} selected. Choose a square.")

    def set_busy(self, busy):
        self.busy = busy
        self._refresh()

    def set_status(self, message):
        self.status.set(message)

    def add_log(self, message):
        self.log.configure(state="normal")
        self.log.insert("end", message.rstrip() + "\n")
        self.log.see("end")
        self.log.configure(state="disabled")
