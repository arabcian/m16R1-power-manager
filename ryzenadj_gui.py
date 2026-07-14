#!/usr/bin/env python3
"""
RyzenAdj GUI - Clean Version
Alienware M16 R1 AMD Power Profile Manager
"""
import os
import sys
import json
import time
import math
import shutil
import tempfile
import subprocess
import threading
import glob
import shlex
from collections import deque
from pathlib import Path

from PySide6.QtCore import Qt, QThread, Signal, QTimer, QProcess, QPointF, QSize
from PySide6.QtGui import QFont, QTextCursor, QColor, QPainter, QBrush, QKeyEvent, QPen, QPainterPath, QIcon
from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QGridLayout, QLabel, QPushButton, QGroupBox, QLineEdit, QTextEdit,
    QFrame, QSplitter, QTabWidget, QSpinBox, QProgressBar, QSizePolicy,
    QComboBox, QInputDialog, QCheckBox, QScrollArea, QSlider, QColorDialog,
    QStyle, QMessageBox
)
from PySide6.QtCharts import QChart, QChartView, QLineSeries, QValueAxis, QScatterSeries

sys.path.insert(0, os.path.dirname(__file__))

try:
    import ryzenadj_wrapper as wrapper
except ImportError:
    print("ERROR: 'ryzenadj_wrapper.py' not found!")
    sys.exit(1)

from tool_paths import find_tool

# Q5: SECURE_MODE / SecureQProcess / run_as_root ölü koddu — import
# ediliyor ve set ediliyordu ama kodun geri kalanında hiç kullanılmıyordu
# (tüm root yolları pkexec + root_helper.py'ye taşınmıştı). Kafa
# karıştırıcı "unsafe mode" uyarısıyla birlikte kaldırıldı.

# ─── FONTS ────────────────────────────────────────────────────────────────
FONT_CANDIDATES = [
    "JetBrains Mono", "Fira Code", "Cascadia Code", "Noto Sans Mono",
    "Source Code Pro", "DejaVu Sans Mono", "Liberation Mono", "Monospace"
]

# C2: Font resolution; exactMatch() is expensive and SL/SE used to call it for every widget.
# Filled in on the first get_font() call after QApplication is ready.
_RESOLVED_FONT_FAMILY: str = ""

def _check_not_root():
    """
    Prevent the GUI from running as root.
    Only the operations that actually need it should get elevated privileges, via Polkit.
    """
    if os.geteuid() == 0:
        print("""
╔═══════════════════════════════════════════════════════════════════╗
║  ❌ ERROR: This application cannot be run with root privileges!  ║
╠═══════════════════════════════════════════════════════════════════╣
║                                                                   ║
║  ✅ CORRECT USAGE:                                                ║
║     $ python3 ryzenadj_gui.py                                     ║
║                                                                   ║
║  WHY?                                                             ║
║  • Running the GUI as root is a massive security hole             ║
║  • pkexec only grants privileges for the operations that need it  ║
║  • First change → password once, then cached for 15 minutes      ║
║                                                                   ║
║  POLKIT SETUP (one time only):                                    ║
║  $ sudo tee /etc/polkit-1/rules.d/51-ryzenadj-gui.rules >/dev/null\
║     << 'EOF'                                                      ║
║  [Copy from the Polkit rule file]                                 ║
║  EOF                                                              ║
║  $ sudo rc-service polkit restart                                 ║
║    (no systemd / on OpenRC systems; on systemd systems use        ║
║     instead: sudo systemctl restart polkit)                       ║
║                                                                   ║
║  For more info: see UYGULAMA_DOKUMANTASYONU_TR.md                 ║
║                                                                   ║
╚═══════════════════════════════════════════════════════════════════╝
        """)
        sys.exit(1)

def get_font(size=8, bold=False):
    # C2: Cache the family name in a module-global variable.
    # Try all candidates once on the first call; on the next ~thousands of calls
    # only QFont(cached_family, size) is created.
    global _RESOLVED_FONT_FAMILY
    if not _RESOLVED_FONT_FAMILY:
        for name in FONT_CANDIDATES:
            probe = QFont(name, 8)
            if probe.exactMatch():
                _RESOLVED_FONT_FAMILY = name
                break
        if not _RESOLVED_FONT_FAMILY:
            _RESOLVED_FONT_FAMILY = "Monospace"
    f = QFont(_RESOLVED_FONT_FAMILY, size)
    f.setBold(bold)
    return f

# ─── COLORS ──────────────────────────────────────────────────────────────
C_BG       = "#141720"
C_BG2      = "#1a1f2e"
C_BG3      = "#0d0f12"
C_BORDER   = "#232b3e"
C_CYAN     = "#00ffe0"
C_ORANGE   = "#fe8019"
C_YELLOW   = "#fabd2f"
C_GREEN    = "#8ec07c"
C_BLUE     = "#83a598"
C_PURPLE   = "#d3869b"
C_GREY     = "#928374"
C_DGREY    = "#7c6f64"
C_VDGREY   = "#504945"
C_LIME     = "#b8bb26"
C_STOP     = "#fb4934"
C_WHITE    = "#ffffff"
C_LIGHT    = "#a89984"

# ─── PER-KEY RGB LAYOUT ──────────────────────────────────────────────────────
# Physical keyboard grid for Alienware M16 R1 TR layout.
# (key_name, colspan_units) — names must match mappings.json entries.
PERKEY_LAYOUT: list[list[tuple[str, int]]] = [
    # Function row
    [("esc",2),("f1",2),("f2",2),("f3",2),("f4",2),("f5",2),("f6",2),("f7",2),
     ("f8",2),("f9",2),("f10",2),("f11",2),("f12",2),("home",2),("end",2),("del",2)],
    # Number row
    [("~",2),("1",2),("2",2),("3",2),("4",2),("5",2),("6",2),("7",2),("8",2),
     ("9",2),("0",2),("-",2),("=",2),("backspace",4)],
    # QWERTY row
    [("tab",3),("q",2),("w",2),("e",2),("r",2),("t",2),("y",2),("u",2),("i",2),
     ("o",2),("p",2),("[",2),("]",2),("\\",3)],
    # Home row
    [("caps",4),("a",2),("s",2),("d",2),("f",2),("g",2),("h",2),("j",2),("k",2),
     ("l",2),(";",2),("'",2),("enter",4)],
    # Shift row
    [("left shift",5),("z",2),("x",2),("c",2),("v",2),("b",2),("n",2),("m",2),
     (",",2),(".",2),("/",2),("right shift",5)],
    # Bottom row
    [("left ctrl",3),("fn",2),("windows",2),("left alt",2),("space",12),
     ("right alt",2),("windows lock",2),("right ctrl",3),
     ("left",2),("up",2),("down",2),("right",2)],
    # Media row (separate narrow strip)
    [("audio mute",3),("volume down",3),("volume up",3),("mic mute",3)],
]

# Aliases: these layout names absorb additional light zones from mappings.json.
PERKEY_ALIASES: dict[str, list[str]] = {
    "backspace":  ["backspace", "backspace2"],
    "caps":       ["caps", "caps2"],
    "left shift": ["left shift", "left shift2"],
    "windows":    ["windows", "windows2"],
    # "space" — mappings may have lid 106+107 with the same name; name-based
    # grouping in _load_alienfx_mappings already merges them.
}


class KeyButton(QPushButton):
    """Per-key RGB button: checkable (selection), color-coded background.

    Attributes:
        key_name:  The key label / mappings.json name.
        lightids:  List of lightid ints for this key (may be multi-zone).
    """

    def __init__(self, key_name: str, lightids: list[int], parent=None):
        super().__init__(parent)
        self.key_name = key_name
        self.lightids = lightids
        self._color: tuple[int, int, int] | None = None
        self._dbl_action = None

        self.setCheckable(True)
        # Abbreviate long names so they fit on narrow keys
        display = key_name if len(key_name) <= 9 else key_name[:8] + "…"
        self.setText(display)
        self.setToolTip(key_name)
        self.setMinimumSize(28, 24)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.setFont(get_font(6))
        # Re-draw border when checked state changes
        self.toggled.connect(lambda _: self._refresh_style())
        self._refresh_style()

    def set_double_click_action(self, fn):
        self._dbl_action = fn

    def mouseDoubleClickEvent(self, event):
        if self._dbl_action:
            self._dbl_action()
        # Don't propagate to avoid re-emitting clicked

    def set_color(self, color: tuple[int, int, int] | None):
        """Set background color; None = unassigned (dark grey)."""
        if color == self._color:
            return  # skip redundant setStyleSheet (B1 optimisation)
        self._color = color
        self._refresh_style()

    def _refresh_style(self):
        checked = self.isChecked()
        if self._color:
            r, g, b = self._color
            bg = f"#{r:02x}{g:02x}{b:02x}"
            lum = 0.299 * r + 0.587 * g + 0.114 * b
            fg = "#000000" if lum > 128 else "#ffffff"
        else:
            bg = C_BG3
            fg = C_DGREY
        bw = "2px" if checked else "1px"
        bc = C_CYAN if checked else C_BORDER
        self.setStyleSheet(
            f"QPushButton{{background:{bg};color:{fg};"
            f"border:{bw} solid {bc};border-radius:2px;"
            f"padding:0px 1px;font-size:6pt;text-align:center;}}")

# C_PASTEL_ORANGE removed — all group box titles now use C_ORANGE

# ─── STYLE ──────────────────────────────────────────────────────────────
GLOBAL_STYLE = f"""
    /* ── Window ────────────────────────────────────────────────── */
    QMainWindow {{ background-color: {C_BG}; }}
    QWidget {{ background-color: {C_BG}; color: #ebdbb2; }}

    /* ── Tabs ──────────────────────────────────────────────────── */
    QTabWidget::pane {{
        border: 1px solid {C_BORDER}; background-color: {C_BG};
    }}
    QTabBar::tab {{
        background-color: {C_BG2}; color: {C_GREY};
        border: 1px solid {C_BORDER}; border-bottom: none;
        padding: 5px 16px; font-size: 8pt; font-weight: bold;
        min-width: 130px; text-transform: uppercase;
    }}
    QTabBar::tab:selected {{
        background-color: {C_BG}; color: {C_CYAN};
        border-top: 1px solid {C_BORDER};
        border-left: 1px solid {C_BORDER};
        border-right: 1px solid {C_BORDER};
        border-bottom: 2px solid {C_CYAN};
    }}
    QTabBar::tab:selected:!focus {{
        background-color: {C_BG}; color: {C_CYAN};
        border-top: 1px solid {C_BORDER};
        border-left: 1px solid {C_BORDER};
        border-right: 1px solid {C_BORDER};
        border-bottom: 2px solid {C_CYAN};
    }}
    QTabBar::tab:hover:!selected {{ color: {C_ORANGE}; }}

    /* ── Group Boxes ──────────────────────────────────────────── */
    QGroupBox {{
        color: {C_ORANGE};
        border: 1px solid {C_BORDER};
        border-radius: 4px;
        margin-top: 8px; padding-top: 6px;
        font-size: 8pt; font-weight: bold;
    }}
    QGroupBox::title {{
        subcontrol-origin: margin; left: 8px; padding: 0 4px;
        color: {C_ORANGE};
    }}

    /* ── Buttons ───────────────────────────────────────────────── */
    QPushButton {{
        background-color: #1e2535; color: #ebdbb2;
        border: 1px solid #2a344d; border-radius: 3px;
        padding: 4px 12px; font-weight: bold; font-size: 8pt;
    }}
    QPushButton:hover {{ background-color: #2a3450; border-color: #3a4a6d; }}
    QPushButton:pressed {{ background-color: #151c2c; }}
    QPushButton#apply_button {{
        background-color: {C_BG}; color: {C_CYAN};
        border: 1px solid {C_CYAN};
    }}
    QPushButton#apply_button:hover {{
        background-color: rgba(0, 255, 224, 30); color: {C_CYAN};
    }}
    QPushButton#apply_button:pressed {{
        background-color: {C_CYAN}; color: {C_BG3};
    }}
    QPushButton#save_button {{
        background-color: #1a2b4c; color: #4a9eff;
        border: 1px solid #2a4478;
    }}
    QPushButton#save_button:hover {{ background-color: #243a5e; }}
    QPushButton#save_button:pressed {{ background-color: #4a9eff; color: {C_BG3}; }}
    QPushButton#run_button {{
        background-color: #1a2b1a; color: {C_GREEN};
        border: 1px solid #2a4a2a;
    }}
    QPushButton#run_button:hover {{ background-color: #243824; }}
    QPushButton#run_button:pressed {{ background-color: {C_GREEN}; color: {C_BG3}; }}
    QPushButton#stop_button {{
        background-color: #2b1a1a; color: {C_STOP};
        border: 1px solid #4a2a2a;
    }}
    QPushButton#stop_button:hover {{ background-color: #3a2424; }}
    QPushButton#stop_button:pressed {{ background-color: {C_STOP}; color: {C_BG3}; }}

    /* ── Line Edit ─────────────────────────────────────────────── */
    QLineEdit {{
        background-color: {C_BG2}; color: {C_YELLOW};
        border: 1px solid {C_BORDER}; border-radius: 2px;
        padding: 2px 4px; font-size: 9pt;
        selection-background-color: #3a4a6d;
    }}
    QLineEdit:focus {{ border: 1px solid {C_CYAN}; }}

    /* ── Spin Box ──────────────────────────────────────────────── */
    QSpinBox {{
        background-color: {C_BG2}; color: {C_YELLOW};
        border: 1px solid {C_BORDER}; border-radius: 2px;
        padding: 1px 4px; font-size: 9pt;
        min-width: 60px;
        selection-background-color: {C_CYAN};
        selection-color: {C_BG};
    }}
    QSpinBox:focus {{ border: 1px solid {C_CYAN}; }}
    QSpinBox::up-button, QSpinBox::down-button {{
        width: 16px; background-color: #1e2535;
        border: none; border-radius: 1px;
    }}
    QSpinBox::up-button:hover, QSpinBox::down-button:hover {{
        background-color: #2a3450;
    }}

    /* ── Combo Box ─────────────────────────────────────────────── */
    QComboBox {{
        background-color: {C_BG2}; color: {C_YELLOW};
        border: 1px solid {C_BORDER}; border-radius: 2px;
        padding: 2px 6px; font-size: 9pt;
    }}
    QComboBox:focus {{ border: 1px solid {C_CYAN}; }}
    QComboBox::drop-down {{
        border: none; width: 18px;
        background-color: #1e2535;
        border-top-right-radius: 2px;
        border-bottom-right-radius: 2px;
    }}
    QComboBox QAbstractItemView {{
        background-color: {C_BG2}; color: #ebdbb2;
        border: 1px solid {C_BORDER};
        selection-background-color: #2a3450;
        selection-color: {C_CYAN};
        outline: none;
    }}

    /* ── Check Box ─────────────────────────────────────────────── */
    QCheckBox {{
        spacing: 5px; color: #ebdbb2; font-size: 8pt;
    }}
    QCheckBox::indicator {{
        width: 14px; height: 14px;
        border: 1px solid {C_BORDER}; border-radius: 2px;
        background-color: {C_BG2};
    }}
    QCheckBox::indicator:checked {{
        background-color: {C_CYAN};
        border-color: {C_CYAN};
    }}
    QCheckBox::indicator:hover {{
        border-color: {C_CYAN};
    }}

    /* ── Sliders ───────────────────────────────────────────────── */
    QSlider::groove:horizontal {{
        background: {C_BG3}; height: 4px;
        border-radius: 2px;
    }}
    QSlider::handle:horizontal {{
        background: {C_CYAN}; width: 14px;
        margin: -5px 0; border-radius: 7px;
    }}
    QSlider::handle:horizontal:hover {{
        background: #33ffe8;
    }}
    QSlider::sub-page:horizontal {{
        background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
            stop:0 rgba(0,255,224,120), stop:1 rgba(0,255,224,60));
        border-radius: 2px;
    }}

    /* ── Progress Bar ──────────────────────────────────────────── */
    QProgressBar {{
        background-color: {C_BG3}; border: 1px solid {C_BORDER};
        border-radius: 3px; text-align: center;
        color: #ebdbb2; font-size: 7pt;
        min-height: 12px; max-height: 14px;
    }}
    QProgressBar::chunk {{
        background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
            stop:0 rgba(0,255,224,180), stop:1 rgba(0,255,224,80));
        border-radius: 2px;
    }}

    /* ── Text Edit ─────────────────────────────────────────────── */
    QTextEdit {{
        background-color: {C_BG3}; color: #a89984;
        border: 1px solid {C_BORDER}; border-radius: 2px;
        font-size: 9pt;
        selection-background-color: #3a4a6d;
    }}

    /* ── Scroll Bars ───────────────────────────────────────────── */
    QScrollArea {{ border: none; background-color: transparent; }}
    QScrollBar:vertical {{
        background-color: {C_BG}; width: 6px; border: none;
    }}
    QScrollBar::handle:vertical {{
        background-color: #2a324d; border-radius: 3px; min-height: 16px;
    }}
    QScrollBar::handle:vertical:hover {{ background-color: #3a4a6d; }}
    QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{
        height: 0px;
    }}
    QScrollBar:horizontal {{
        background-color: {C_BG}; height: 6px; border: none;
    }}
    QScrollBar::handle:horizontal {{
        background-color: #2a324d; border-radius: 3px;
    }}
    QScrollBar::handle:horizontal:hover {{ background-color: #3a4a6d; }}
    QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {{
        width: 0px;
    }}

    /* ── Splitter ──────────────────────────────────────────────── */
    QSplitter::handle {{ background-color: {C_BORDER}; }}

    /* ── Tooltip ───────────────────────────────────────────────── */
    QToolTip {{
        background-color: {C_BG2}; color: #ebdbb2;
        border: 1px solid {C_BORDER}; padding: 3px;
        font-size: 8pt;
    }}
"""

# ─── HELPER WIDGETS ──────────────────────────────────────────────────
class SL(QLabel):
    def __init__(self, text="", bold=False, color=None, size=8, selectable=False):
        super().__init__(text)
        self.setFont(get_font(size, bold))
        if color:
            self.setStyleSheet(f"color: {color};")
        if selectable:
            self.setTextInteractionFlags(Qt.TextSelectableByMouse)

class SE(QLineEdit):
    def __init__(self, text="", width=58, placeholder=""):
        super().__init__(text)
        self.setFixedWidth(width)
        self.setFont(get_font(8))
        if placeholder:
            self.setPlaceholderText(placeholder)
        self._normal()

    def _normal(self):
        self.setStyleSheet(f"""
            QLineEdit {{
                background-color: {C_BG2}; color: {C_YELLOW};
                border: 1px solid {C_BORDER}; border-radius: 2px; padding: 1px 3px;
            }}
            QLineEdit:focus {{ border: 1px solid {C_CYAN}; }}
        """)

    def set_grey(self, on: bool):
        if on:
            self.setStyleSheet(f"""
                QLineEdit {{
                    background-color: {C_BG3}; color: {C_VDGREY};
                    border: 1px solid #1a1f2e; border-radius: 2px; padding: 1px 3px;
                }}
            """)
        else:
            self._normal()

def hsep():
    f = QFrame()
    f.setFrameShape(QFrame.HLine)
    f.setStyleSheet(f"color: {C_BORDER}; margin: 1px 0;")
    return f

# ─── PROFILE ICON (Vector, single-color power profile icon) ────────────
# Emoji glyphs (🍃❄️☯🚀🔥⚡⚙) have different intrinsic
# size/baseline depending on font/platform, and some are multi-color (full-color emoji),
# so they can't be recolored with setStyleSheet. This was causing
# alignment drift between buttons and an "amateurish" look. ProfileIcon
# draws all profiles on the SAME fixed-size canvas with QPainter,
# single-colored/vectorized, so pixel-perfect alignment
# and full consistency with the theme color is guaranteed.
class ProfileIcon(QWidget):
    # Represents power intensity as a "gauge needle" angle (Alienware
    # Command Center / ASUS Armoury Crate style unified visual language).
    # Angle increases in the Qt axis convention: 0°=right, 90°=up, 180°=left.
    _GAUGE_ANGLES = {
        "quiet": 200,
        "cool": 160,
        "balanced": 90,
        "balanced-performance": 45,
        "performance": -20,
        "overdrive": -20,
    }

    def __init__(self, kind: str, color: str, size: int = 22, parent=None):
        super().__init__(parent)
        self._kind = kind
        self._color = QColor(color)
        self.setFixedSize(size, size)
        self.setAttribute(Qt.WA_TransparentForMouseEvents)
        self.setAttribute(Qt.WA_TranslucentBackground)

    def set_color(self, color: str):
        self._color = QColor(color)
        self.update()

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)

        pen = QPen(self._color)
        pen.setWidthF(1.6)
        pen.setCapStyle(Qt.RoundCap)
        pen.setJoinStyle(Qt.RoundJoin)
        p.setPen(pen)
        p.setBrush(Qt.NoBrush)

        rect = self.rect().adjusted(2, 2, -2, -2)

        if self._kind == "custom":
            self._draw_gear(p, rect)
        else:
            self._draw_gauge(p, rect, self._kind)

        p.end()

    def _draw_gauge(self, p: QPainter, rect, kind: str):
        arc_rect = rect.adjusted(1, 2, -1, 0)
        # Half-circle gauge arc from 210° to -30° (240° span)
        p.drawArc(arc_rect, 210 * 16, -240 * 16)

        cx = arc_rect.center().x()
        cy = arc_rect.center().y()
        r = arc_rect.width() / 2.0 - 1.5

        deg = self._GAUGE_ANGLES.get(kind, 90)
        rad = math.radians(deg)
        x2 = cx + r * math.cos(rad)
        y2 = cy - r * math.sin(rad)

        p.drawLine(QPointF(cx, cy), QPointF(x2, y2))
        p.setBrush(self._color)
        p.drawEllipse(QPointF(cx, cy), 1.5, 1.5)

    def _draw_gear(self, p: QPainter, rect):
        cx, cy = rect.center().x(), rect.center().y()
        r_outer = rect.width() / 2.0 - 1.0
        r_inner = r_outer * 0.62
        teeth = 8

        path = QPainterPath()
        for i in range(teeth * 2):
            angle = math.pi * i / teeth
            r = r_outer if i % 2 == 0 else r_inner
            x = cx + r * math.cos(angle)
            y = cy + r * math.sin(angle)
            if i == 0:
                path.moveTo(x, y)
            else:
                path.lineTo(x, y)
        path.closeSubpath()

        p.drawPath(path)
        p.drawEllipse(QPointF(cx, cy), r_inner * 0.42, r_inner * 0.42)

# ─── V/F CURVE WIDGET ──────────────────────────────────────────────
class VFCurveWidget(QChartView):
    pointClicked = Signal(int, int)
    pointDragged = Signal(int, int)
    pointReleased = Signal(int, int)
    selectionChanged = Signal()

    def __init__(self, points=None, parent=None):
        super().__init__(parent)
        self.setRenderHint(QPainter.Antialiasing)
        self._group_drag = False
        self._group_drag_start_y = 0
        self.setFocusPolicy(Qt.StrongFocus)
        self.base_points = [(700 + i * 4, 1500 + i * 2) for i in range(127)]
        self.points = points if points else self.base_points.copy()
        self.base_freqs = [f for _, f in self.points]
        self.selected_indices = set()
        self.current_index = 0
        self.dragging_index = -1
        self.chart = QChart()
        self.chart.setAnimationOptions(QChart.SeriesAnimations)
        self.chart.legend().hide()
        self.chart.setBackgroundBrush(QColor(10, 10, 10))
        self.chart.setBackgroundVisible(True)
        self.setChart(self.chart)
        self._create_series()
        self.setInteractive(True)
        self.setMinimumHeight(300)

    def _create_series(self):
        self.chart.removeAllSeries()
        self.line_series = QLineSeries()
        for v, f in self.points:
            self.line_series.append(v, f)
        self.chart.addSeries(self.line_series)

        self.scatter_series = QScatterSeries()
        self.scatter_series.setMarkerSize(7)
        self.scatter_series.setColor(QColor(C_CYAN))
        self.scatter_series.setBorderColor(QColor(C_ORANGE))
        self.scatter_series.clicked.connect(self._on_point_clicked)
        for v, f in self.points:
            self.scatter_series.append(v, f)
        self.chart.addSeries(self.scatter_series)

        self.selected_series = QScatterSeries()
        self.selected_series.setMarkerSize(10)
        self.selected_series.setColor(QColor(C_YELLOW))
        self.selected_series.setBorderColor(QColor(C_WHITE))
        self.selected_series.clicked.connect(self._on_point_clicked)
        for i in self.selected_indices:
            if i < len(self.points):
                v, f = self.points[i]
                self.selected_series.append(v, f)
        self.chart.addSeries(self.selected_series)

        self.axis_x = QValueAxis()
        self.axis_x.setTitleText("Voltage (mV)")
        self.axis_x.setRange(600, 1200)
        self.axis_x.setTickCount(10)
        self.axis_x.setLabelsColor(QColor(C_LIGHT))
        self.axis_x.setTitleBrush(QBrush(QColor(C_LIGHT)))
        self.chart.addAxis(self.axis_x, Qt.AlignBottom)
        self.line_series.attachAxis(self.axis_x)
        self.scatter_series.attachAxis(self.axis_x)
        self.selected_series.attachAxis(self.axis_x)

        self.axis_y = QValueAxis()
        self.axis_y.setTitleText("Frequency (MHz)")
        self.axis_y.setRange(800, 3000)
        self.axis_y.setTickCount(12)
        self.axis_y.setLabelsColor(QColor(C_LIGHT))
        self.axis_y.setTitleBrush(QBrush(QColor(C_LIGHT)))
        self.chart.addAxis(self.axis_y, Qt.AlignLeft)
        self.line_series.attachAxis(self.axis_y)
        self.scatter_series.attachAxis(self.axis_y)
        self.selected_series.attachAxis(self.axis_y)
        font = QFont()
        font.setPointSize(7)
        self.axis_y.setLabelsFont(font)
        self.axis_x.setLabelFormat("%.0f")
        self.axis_y.setLabelFormat("%.0f")

    def _on_point_clicked(self, point):
        for i, (v, f) in enumerate(self.points):
            if abs(v - point.x()) < 1 and abs(f - point.y()) < 1:
                self.selected_indices.clear()
                self.selected_indices.add(i)
                self.current_index = i
                self._update_series()
                self.pointClicked.emit(i, int(point.y()))
                self.selectionChanged.emit()
                break

    def set_points(self, points, base_freqs=None):
        self.points = points
        self.base_freqs = base_freqs if base_freqs else [f for _, f in points]
        self.selected_indices.clear()
        self.current_index = 0
        self._update_series()

    def update_points(self, points):
        self.points = points
        self._update_series()

    def set_point_freq(self, index, new_freq):
        v, _ = self.points[index]
        self.points[index] = (v, int(new_freq))
        self._update_series()

    def get_points(self):
        return self.points

    def get_offset(self, index):
        if index < len(self.points) and index < len(self.base_freqs):
            return self.points[index][1] - self.base_freqs[index]
        return 0

    def reset_to_base(self):
        self.set_points(self.base_points.copy(), self.base_freqs.copy())

    def _update_series(self):
        # B5: replace() = one batched update signal (append() emits a separate signal per point).
        # During dragging this produces 3 signals instead of 24 per mouse-move.
        sel_pts = [QPointF(self.points[i][0], self.points[i][1])
                   for i in self.selected_indices if i < len(self.points)]
        all_pts = [QPointF(v, f) for v, f in self.points]

        self.selected_series.replace(sel_pts)
        self.scatter_series.replace(all_pts)
        self.line_series.replace(all_pts)
        # B6: selectionChanged is only emitted here;
        # the calling select_next/prev/clear/all methods no longer emit it again.
        self.selectionChanged.emit()

    def select_next(self):
        if not self.points:
            return
        self.current_index = (self.current_index + 1) % len(self.points)
        self.selected_indices.add(self.current_index)
        self._update_series()   # B6: emit happens here, not repeated

    def select_prev(self):
        if not self.points:
            return
        self.current_index = (self.current_index - 1) % len(self.points)
        self.selected_indices.add(self.current_index)
        self._update_series()   # B6

    def clear_selection(self):
        self.selected_indices.clear()
        self.current_index = 0
        self._update_series()   # B6

    def select_all(self):
        self.selected_indices = set(range(len(self.points)))
        self.current_index = 0
        self._update_series()   # B6

    def adjust_selected_offset(self, delta):
        if not self.selected_indices:
            return
        for idx in self.selected_indices:
            v, f = self.points[idx]
            new_f = max(800, min(3000, f + delta))
            self.points[idx] = (v, int(new_f))
        self._update_series()
        for idx in self.selected_indices:
            self.pointDragged.emit(idx, self.points[idx][1])

    # B5: last update time, for mouse-move throttling
    _last_drag_ms = 0

    def mousePressEvent(self, event):
        pos = event.position().toPoint()
        min_dist = 30
        self.dragging_index = -1
        self._group_drag = False
        hit_index = -1
        for i, (v, f) in enumerate(self.points):
            scene_pos = self.chart.mapToPosition(QPointF(v, f))
            dx = pos.x() - scene_pos.x()
            dy = pos.y() - scene_pos.y()
            if (dx*dx + dy*dy) < min_dist*min_dist:
                hit_index = i
                break
        if hit_index != -1:
            if hit_index in self.selected_indices:
                self.dragging_index = hit_index
                self.setCursor(Qt.ClosedHandCursor)
        else:
            if len(self.selected_indices) == len(self.points):
                self._group_drag = True
                self._group_drag_start_y = pos.y()
                self.setCursor(Qt.ClosedHandCursor)
            else:
                self.clear_selection()
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        # B5: don't update more often than ~16 ms (60 fps) apart while dragging
        if self.dragging_index != -1 or self._group_drag:
            now = int(time.monotonic() * 1000)
            if now - self._last_drag_ms < 16:
                super().mouseMoveEvent(event)
                return
            self._last_drag_ms = now

        if self.dragging_index != -1:
            pos = event.position().toPoint()
            scene_pos = self.chart.mapToValue(QPointF(pos.x(), pos.y()))
            new_f = int(max(800, min(3000, scene_pos.y())))
            self.set_point_freq(self.dragging_index, new_f)
            self.pointDragged.emit(self.dragging_index, new_f)
        elif self._group_drag:
            pos = event.position().toPoint()
            start_pos = QPointF(0, self._group_drag_start_y)
            current_pos = QPointF(0, pos.y())
            start_value = self.chart.mapToValue(start_pos).y()
            current_value = self.chart.mapToValue(current_pos).y()
            delta_freq = int(current_value - start_value)
            if delta_freq != 0:
                for i in range(len(self.points)):
                    v, f = self.points[i]
                    self.points[i] = (v, max(800, min(3000, f + delta_freq)))
                self._update_series()
                self.pointDragged.emit(-1, 0)
                self._group_drag_start_y = pos.y()
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        if self.dragging_index != -1:
            self.setCursor(Qt.ArrowCursor)
            self.pointReleased.emit(self.dragging_index, self.points[self.dragging_index][1])
            self.dragging_index = -1
        elif self._group_drag:
            self.setCursor(Qt.ArrowCursor)
            self._group_drag = False
        super().mouseReleaseEvent(event)

    def keyPressEvent(self, event: QKeyEvent):
        if event.key() == Qt.Key_Right:
            self.select_next()
        elif event.key() == Qt.Key_Left:
            self.select_prev()
        elif event.key() == Qt.Key_Up:
            self.adjust_selected_offset(1)
        elif event.key() == Qt.Key_Down:
            self.adjust_selected_offset(-1)
        elif event.key() == Qt.Key_Space:
            if self.current_index in self.selected_indices:
                self.selected_indices.remove(self.current_index)
            else:
                self.selected_indices.add(self.current_index)
            self._update_series()
            self.selectionChanged.emit()
        elif event.key() == Qt.Key_Escape:
            self.clear_selection()
        else:
            super().keyPressEvent(event)

    def update_from_points_data(self, points_data):
        """Updates clamped-point detection, the reference line, and the axes using points_data."""
        if not points_data:
            return

        # Update the internal points and base_freqs lists
        self.points = [(p["volt_mv"], p["freq_mhz"]) for p in points_data]
        self.base_freqs = [p["freq_mhz"] - p["delta_mhz"] for p in points_data]

        # Clamped tespit
        clamped = self._detect_clamped_points(points_data)

        # Axis ranges
        v_min, v_max = self._volt_extent(points_data)
        f_min, f_max = self._freq_extent(points_data)

        # Update the existing axes
        self.axis_x.setRange(v_min, v_max)
        self.axis_y.setRange(f_min, f_max)

        # Rebuild the existing series (clamped points shown in a different color)
        self._update_series_with_clamped(points_data, clamped)

    def _detect_clamped_points(self, points_data):
        """Detects clamped points (placeholder)."""
        # Return empty for now
        return []

    def _volt_extent(self, points_data):
        """Returns the voltage range."""
        if not points_data:
            return 600, 1200
        voltages = [p["volt_mv"] for p in points_data]
        return min(voltages) - 50, max(voltages) + 50

    def _freq_extent(self, points_data):
        """Returns the frequency range."""
        if not points_data:
            return 800, 3000
        freqs = [p["freq_mhz"] for p in points_data]
        return min(freqs) - 200, max(freqs) + 200

    def _update_series_with_clamped(self, points_data, clamped):
        """Shows clamped points in a different color (placeholder)."""
        # For now just update the existing series
        self._update_series()

# ─── C1: ASYNC SYSINFO LOADER ────────────────────────────────────────────
# The read_sys_info() call used to block the GUI thread for as long as the
# corefreq-cli + lspci timeout (up to 10 s total). Fix: background thread.
class SysInfoWorker(QThread):
    ready = Signal(dict)

    def run(self):
        try:
            info = read_sys_info()
        except Exception:
            info = {}
        self.ready.emit(info)

# NOT: Burada daha önce kullanılmayan bir `ProfileWorker(QThread)` sınıfı
# vardı — tanımlıydı ama kodun hiçbir yerinde örneklenmiyordu (asıl
# profil uygulama akışı _apply_profile() içinde senkron yapılıyor). Ölü
# kod olduğu için kaldırıldı.

class TelemetryWorker(QThread):
    data = Signal(str, str, str, dict, list, float, float)
    error = Signal(str)  # K5: sessizce yutulan hatalar için

    def __init__(self):
        super().__init__()
        self.running = True
        self.last_stat = []
        self.prev_energy = None   # A3: None → just record on the first tick, no spike
        self.prev_time   = None   # A8: we'll use monotonic
        self.hwmon_dir = self._find_zenergy_hwmon()
        # Detect and cache the energy (Esocket0) file path once
        self.energy_file_path = self._find_energy_file() if self.hwmon_dir else None

        # File objects that stay open persistently (Persistent File Handles)
        self._f_stat = None
        self._f_gov = None
        self._f_epp = None
        self._f_driver = None
        self._f_plat_prof = None
        self._f_energy = None
        self.cpu_freq_paths = []
        self.cpu_freq_handles = {}

    def _find_zenergy_hwmon(self):
        hwmon_base = '/sys/class/hwmon'
        try:
            for hwmon in os.listdir(hwmon_base):
                hwmon_path = os.path.join(hwmon_base, hwmon)
                name_path = os.path.join(hwmon_path, 'name')
                if os.path.exists(name_path):
                    with open(name_path, 'r') as f:
                        name = f.read().strip()
                        if name == 'zenergy':
                            return hwmon_path
        except Exception:
            pass
        return None

    def _find_energy_file(self):
        if not self.hwmon_dir:
            return None
        energy_file = os.path.join(self.hwmon_dir, 'energy25_input')
        if os.path.exists(energy_file):
            return energy_file
        try:
            energy_files = glob.glob(os.path.join(self.hwmon_dir, 'energy*_input'))
            for fpath in energy_files:
                label_path = fpath.replace('_input', '_label')
                if os.path.exists(label_path):
                    with open(label_path, 'r') as f:
                        if f.read().strip() == 'Esocket0':
                            return fpath
        except Exception:
            pass
        return None

    def get_stat(self):
        cpus = []
        try:
            # Open the file the first time we encounter it and keep it open
            if self._f_stat is None:
                if os.path.exists('/proc/stat'):
                    self._f_stat = open('/proc/stat', 'r')
            if self._f_stat:
                self._f_stat.seek(0)
                content = self._f_stat.read()
                for l in content.splitlines():
                    if l.startswith('cpu') and len(l) > 3 and l[3].isdigit():
                        parts = list(map(int, l.split()[1:]))
                        idle  = parts[3] + parts[4]
                        # A7: guest/guest_nice (indeks 8,9) Linux'ta zaten
                        # counted within user → to avoid double counting
                        # only gather user..steal (the first 8 fields).
                        total = sum(parts[:8])
                        cpus.append((idle, total))
        except Exception:
            if self._f_stat:
                try: self._f_stat.close()
                except Exception: pass
                self._f_stat = None
        return cpus

    def _read_sys_file(self, attr_name, filepath, default="N/A"):
        """Safe helper that reads via seek(0) instead of repeatedly opening/closing virtual files"""
        try:
            f = getattr(self, attr_name, None)
            if f is None:
                if os.path.exists(filepath):
                    f = open(filepath, 'r')
                    setattr(self, attr_name, f)
            if f:
                f.seek(0)
                return f.read().strip()
        except Exception:
            f = getattr(self, attr_name, None)
            if f:
                try: f.close()
                except Exception: pass
                setattr(self, attr_name, None)
        return default

    def run(self):
        try:
            while self.running:
                try:
                    cpu = wrapper.get_cpu_temperature_live()
                    gpu = wrapper.get_gpu_temperature_live()
                    fans = wrapper.get_fan_speeds_live()
                    boost = wrapper.get_all_fan_boost_values()

                    if cpu <= 59:
                        col = C_CYAN
                    elif cpu <= 85:
                        col = C_YELLOW
                    else:
                        col = C_STOP

                    gpu_s = f"GPU: {gpu:.1f}°C" if gpu > 0 else "GPU: N/A"
                    bp = [f"{n}{v}%" for n, v in boost.items() if v > 0]
                    bs = " | Boost: " + (" ".join(bp)) if bp else " | Boost: OFF"
                    status = (f" 📊 CPU: {cpu:.1f}°C | {gpu_s} | "
                              f"Fans: CPU {fans.get('CPU', 0)} | GPU {fans.get('GPU', 0)} | "
                              f"Mid {fans.get('Mid', 0)} | Side {fans.get('Side', 0)}{bs}")

                    active = ""
                    # D1: use open() directly instead of TOCTOU (exists+open); catch FileNotFoundError.
                    for sp in ["/tmp/ryzenadj_active_profile.state", "/tmp/ryzenadj_current_profile.state"]:
                        try:
                            with open(sp, 'r') as pf:
                                v = pf.read().strip()
                                if v:
                                    active = v
                                    break
                        except (FileNotFoundError, OSError):
                            pass
                    if not active:
                        active = self._read_sys_file('_f_plat_prof', "/sys/firmware/acpi/platform_profile", "UNKNOWN")

                    stat_new = self.get_stat()
                    usage_pcts = []
                    if self.last_stat:
                        for (i1, t1), (i2, t2) in zip(self.last_stat, stat_new):
                            d_idle = i2 - i1
                            d_tot = t2 - t1
                            if d_tot > 0:
                                usage_pcts.append(100.0 * (d_tot - d_idle) / d_tot)
                            else:
                                usage_pcts.append(0.0)
                    else:
                        usage_pcts = [0.0] * len(stat_new)
                    self.last_stat = stat_new

                    gov = self._read_sys_file('_f_gov', "/sys/devices/system/cpu/cpu0/cpufreq/scaling_governor")
                    epp = self._read_sys_file('_f_epp', "/sys/devices/system/cpu/cpu0/cpufreq/energy_performance_preference")
                    driver = self._read_sys_file('_f_driver', "/sys/devices/system/cpu/cpu0/cpufreq/scaling_driver")
                    cpu_params = {"gov": gov, "epp": epp, "driver": driver}

                    watt = 0.0
                    if self.energy_file_path:
                        try:
                            if self._f_energy is None:
                                if os.path.exists(self.energy_file_path):
                                    self._f_energy = open(self.energy_file_path, 'r')
                            if self._f_energy:
                                self._f_energy.seek(0)
                                raw_val = self._f_energy.read().strip()
                                if raw_val:
                                    current_energy = int(raw_val) / 1_000_000
                                    current_time   = time.monotonic()  # A8: unaffected by NTP jumps
                                    if self.prev_energy is not None and self.prev_time is not None:
                                        energy_delta = current_energy - self.prev_energy
                                        time_delta   = current_time   - self.prev_time
                                        # A4: counter wraparound → negative delta → skip
                                        if energy_delta < 0:
                                            pass  # update prev, skip this tick
                                        elif time_delta > 0:
                                            watt = energy_delta / time_delta
                                    # A3: just record on the first tick (watt stays 0, no spike)
                                    self.prev_energy = current_energy
                                    self.prev_time   = current_time
                        except Exception:
                            if self._f_energy:
                                try: self._f_energy.close()
                                except Exception: pass
                                self._f_energy = None

                    avg_freq = 0.0
                    try:
                        # Scan core frequency paths once; removes glob.glob overhead every loop
                        if not self.cpu_freq_paths:
                            self.cpu_freq_paths = sorted(glob.glob('/sys/devices/system/cpu/cpu*/cpufreq/scaling_cur_freq'))

                        freqs = []
                        for path in self.cpu_freq_paths:
                            f = self.cpu_freq_handles.get(path)
                            if f is None:
                                try:
                                    if os.path.exists(path):
                                        f = open(path, 'r')
                                        self.cpu_freq_handles[path] = f
                                except Exception:
                                    f = None
                            if f:
                                try:
                                    f.seek(0)
                                    val = f.read().strip()
                                    if val:
                                        freqs.append(int(val))
                                except Exception:
                                    try: f.close()
                                    except Exception: pass
                                    self.cpu_freq_handles[path] = None
                        if freqs:
                            avg_freq = sum(freqs) / len(freqs) / 1000
                    except Exception:
                        pass

                    self.data.emit(status, col, active.upper(), cpu_params, usage_pcts, watt, avg_freq)
                except Exception as e:
                    # K5: Bu en dıştaki except eskiden hatayı tamamen
                    # yutuyordu (panel sessizce donuyor, iz kalmıyordu).
                    # Artık en azından rate-limited (en fazla 10 saniyede
                    # bir) bir defa traceback ile loglanıyor, döngü yine
                    # de öldürülmüyor.
                    now = time.monotonic()
                    if now - getattr(self, "_last_error_log_time", 0.0) > 10.0:
                        self._last_error_log_time = now
                        import traceback
                        tb = traceback.format_exc()
                        try:
                            self.error.emit(f"⚠️ Telemetry loop error: {e}\n{tb}")
                        except Exception:
                            pass
                for _ in range(20):
                    if self.isInterruptionRequested():
                        break
                    time.sleep(0.1)
        finally:
            # All persistent handles left open are cleaned up when the thread stops
            self._cleanup_handles()

    def _cleanup_handles(self):
        handles = [self._f_stat, self._f_gov, self._f_epp, self._f_driver, self._f_plat_prof, self._f_energy]
        for f in handles:
            if f:
                try: f.close()
                except Exception: pass
        if hasattr(self, 'cpu_freq_handles'):
            for f in self.cpu_freq_handles.values():
                if f:
                    try: f.close()
                    except Exception: pass

    def stop(self):
        self.running = False
        self.requestInterruption()

# ─── SYSINFO ──────────────────────────────────────────────────────────
def read_sys_info() -> dict:
    info = {}
    # C3: /proc/cpuinfo is read once; model name + core/thread count
    # are extracted in a single pass (previously there were two separate read_text() calls).
    try:
        cpu_model  = "Unknown CPU"
        cores      = 0
        threads    = 0
        for ln in Path("/proc/cpuinfo").read_text().splitlines():
            if ln.startswith("model name") and cpu_model == "Unknown CPU":
                cpu_model = ln.split(":", 1)[1].strip()
            elif ln.startswith("cpu cores"):
                try: cores   = max(cores,   int(ln.split(":")[1]))
                except ValueError: pass
            elif ln.startswith("siblings"):
                try: threads = max(threads, int(ln.split(":")[1]))
                except ValueError: pass
        info["cpu"]     = cpu_model
        info["cores"]   = cores
        info["threads"] = threads
    except Exception:
        info["cpu"]     = "Unknown CPU"
        info["cores"]   = 0
        info["threads"] = 0
    try:
        lspci_path = find_tool("lspci")
        gpus = []
        if lspci_path:
            r = subprocess.run([lspci_path, "-mm"], capture_output=True, text=True, timeout=5)
            for ln in r.stdout.splitlines():
                if "VGA" in ln or "3D" in ln or "Display" in ln:
                    p = ln.split('"')
                    gpus.append(f"{p[3]} {p[5]}" if len(p) >= 6 else ln.strip())
        info["gpus"] = gpus[:2]
    except Exception:
        info["gpus"] = []

    info["ram_lines"] = []

    # --- Baseline: capacity from /proc/meminfo (always available) ---
    _mem_cap_str = "Unknown"
    try:
        for ln in Path("/proc/meminfo").read_text().splitlines():
            if ln.startswith("MemTotal:"):
                _kb = int(ln.split()[1])
                _gb = round(_kb / 1024 / 1024, 1)
                _mem_cap_str = f"{_gb} GB"
                break
    except Exception:
        pass

    # --- Try corefreq-cli -j for richer info ---
    # Verified JSON layout:
    #   Uncore.CtrlSpeed                        -> MT/s (e.g. 5200)
    #   Uncore.Unit.DDR_Ver                     -> DDR generation (e.g. 5)
    #   Uncore.MC[i].ChannelCount               -> active channels per controller
    #   Uncore.MC[i].Channel[j].DIMM[k].Size   -> per-RANK MB (not total DIMM!)
    #   Uncore.MC[i].Channel[j].DIMM[k].Ranks  -> ranks -> Size*Ranks = real DIMM size
    #   Uncore.MC[i].Channel[j].Timing.*        -> tCL, tRCD_R, tRP
    #   SysGate.memInfo.totalram                -> kernel total RAM in KB (most accurate)
    _cf_rich = False
    corefreq_cli_path = find_tool("corefreq-cli")
    try:
        if not corefreq_cli_path:
            raise FileNotFoundError  # fall straight to the /proc/meminfo baseline below
        r = subprocess.run([corefreq_cli_path, "-j"], capture_output=True, text=True,
                           encoding='utf-8', timeout=5)
        if r.stdout:
            j_data = json.loads(r.stdout)
            uncore     = j_data.get("Uncore", {})
            mc_list    = uncore.get("MC", [])
            ctrl_speed = uncore.get("CtrlSpeed", 0)
            ddr_ver    = uncore.get("Unit", {}).get("DDR_Ver", 0)

            if mc_list and ctrl_speed:
                # capacity: kernel-reported total is most reliable
                # C4: 'import math' was unnecessary here; already imported at module level
                mem_info = j_data.get("SysGate", {}).get("memInfo", {})
                total_kb = mem_info.get("totalram", 0)
                if total_kb > 0:
                    raw_gb  = total_kb / 1024 / 1024
                    snap_gb = 2 ** round(math.log2(raw_gb))
                    _mem_cap_str = f"{snap_gb} GB"

                # per-DIMM breakdown: Size is per-rank -> multiply by Ranks
                dimm_sizes_gb  = []
                total_channels = 0
                first_timing   = {}
                for ctrl in mc_list:
                    ch_count = ctrl.get("ChannelCount", 0)
                    if ch_count == 0:
                        continue
                    total_channels += ch_count
                    for ch in ctrl.get("Channel", []):
                        if not first_timing:
                            first_timing = ch.get("Timing", {})
                        for dimm in ch.get("DIMM", []):
                            sz_rank = dimm.get("Size", 0)
                            ranks   = dimm.get("Ranks", 1) or 1
                            if sz_rank and sz_rank > 0:
                                dimm_sizes_gb.append(round(sz_rank * ranks / 1024))

                cl   = first_timing.get("tCL",   0)
                trcd = first_timing.get("tRCD_R", 0)
                trp  = first_timing.get("tRP",   0)

                if total_channels > 0 and ctrl_speed > 0:
                    ddr_label = f"DDR{ddr_ver}" if ddr_ver else "DDR"
                    ch_label  = {1: "Single", 2: "Dual", 4: "Quad"}.get(
                                    total_channels, str(total_channels) + "x") + " Channel"

                    if dimm_sizes_gb and len(set(dimm_sizes_gb)) == 1:
                        dimm_str = f"{len(dimm_sizes_gb)}x {dimm_sizes_gb[0]} GB"
                    elif dimm_sizes_gb:
                        dimm_str = " + ".join(f"{x} GB" for x in dimm_sizes_gb)
                    else:
                        dimm_str = ""

                    line1 = f"Capacity:  {_mem_cap_str}" + (f"  .  {dimm_str}" if dimm_str else "")
                    line2 = f"{ddr_label}-{ctrl_speed} MT/s  .  {ch_label}"
                    line3 = f"CL{cl}-{trcd}-{trp}" if cl else ""

                    info["ram_lines"] = [line1, line2] + ([line3] if line3 else [])
                    _cf_rich = True
    except Exception:
        pass  # corefreq not available -> fall through to /proc/meminfo baseline

    if not _cf_rich:
        # corefreq didn't give channel/freq data; show what we have
        info["ram_lines"] = [
            f"Capacity: {_mem_cap_str}",
            "Type/Speed: run corefreqd" if _mem_cap_str != "Unknown" else "Type/Speed: unknown",
        ]

    try:
        info["kernel"] = os.uname().release  # no subprocess needed for this
    except Exception:
        info["kernel"] = "Unknown"
    try:
        for ln in Path("/etc/os-release").read_text().splitlines():
            if ln.startswith("PRETTY_NAME="):
                info["distro"] = ln.split("=", 1)[1].strip().strip('"')
                break
    except Exception:
        info["distro"] = "Linux"
    return info

# ─── MAIN WINDOW ──────────────────────────────────────────────────────
class RyzenAdjGUI(QMainWindow):
    log_signal = Signal(str)
    _rgb_detect_done = Signal(int, str)
    _nvml_init_done = Signal(object, object, str)
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Alienware M16 R1 AMD — Power Profile Manager")
        self.setFixedSize(1280, 560)
        self.setStyleSheet(GLOBAL_STYLE)
        self.log_signal.connect(self._safe_log_append)
        self._rgb_detect_done.connect(self._on_rgb_detect_done)
        self._nvml_init_done.connect(self._on_nvml_init_done)

        self.profiles = wrapper.PROFILES
        self.current = None
        self.config = {}
        self.cores = []
        self.dirty = False
        self._co_core_wgts = []
        self._coall_entry = None
        self._active_profile = ""
        self._cf_process = None
        self._cf_timer = None
        self._cppc_timer = None
        self._cppc_fetching = False
        # ─── CO Live Telemetry ─────────────────────────────────────────
        self._co_live_timer = None
        self._co_live_handles: dict = {}          # {path: file_obj}
        self._co_live_core_prev: dict = {}        # {epath: (joules, monotonic)}
        self._co_live_socket_prev = None          # (joules, monotonic)
        self._co_live_socket_buf = deque(maxlen=10)   # D6: rolling 10-sample watt buffer
        # D3: last (text, style-color) cache per CO Live label, keyed by id(widget)
        self._co_live_text_cache: dict = {}
        self._co_live_style_cache: dict = {}
        # D5: cached "█" advance width for the bar font (invalidated on resize)
        self._co_bar_char_w = 0
        self.gpu_info_timer = None
        self.root_process = None
        self.root_output = ""
        # ─── CPU Core Isolation (CCX/CCD split) ────────────────────────
        self.isolation_process = None
        self.isolation_output = ""
        self.isolation_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "redirect-tasks")
        self.isolation_apply_script = os.path.join(self.isolation_dir, "redirect-tasks.sh")
        self.isolation_revert_script = os.path.join(self.isolation_dir, "revert-tasks.sh")
        self._default_points = []
        self._default_base_freqs = []
        self._point_offsets = {}
        self._read_offsets = {}
        self._core_offset = 0
        self._flatten_threshold = -1
        self._gpu_indices = []
        self._curve_modified = False
        self.edit_mode = False
        self.edit_profile = None
        # G-MODE and extra settings
        self.gmode_combo = None
        self.gmode_active = False
        self.gmode_process = None
        self.extra_settings_path = Path.home() / ".config/ryzenadj_gui/extra_settings.json"
        self.extra_settings = {}
        # ─── AlienFX RGB ───────────────────────────────────────────────
        self._alienfx_cli = self._find_alienfx_cli()   # helper method — just a PATH/candidate lookup, cheap
        # Startup perf fix A: querying alienfx_cli does a live USB HID device
        # enumeration and can take up to its 4s timeout. Don't block window
        # creation on it — start with a placeholder and detect in the
        # background once the window is already on screen (see
        # _kick_off_background_detection, called after show()).
        self._rgb_dev_count = 0
        self._rgb_cmd_preview = None   # will be used as a QLabel
        self._rgb_log = None           # no log widget (writes to main _log instead)
        self._rgb_process = None          # single live alienfx_cli QProcess
        self._rgb_last_color = (0, 0, 0)  # preview cache (r, g, b)
        # ─── RGB Command Queue (prevents USB HID lock contention) ─────────
        self._rgb_cmd_queue = []          # bekleyen komut listesi
        self._rgb_queue_busy = False      # is the queue currently processing?
        # ─── RGB Profile Save/Load ─────────────────────────────────────
        self._rgb_profiles_path = Path.home() / ".config/ryzenadj_gui/rgb_profiles.json"
        self._rgb_cmd_history = []        # command history — for saving profiles
        self._rgb_profiles = {}           # {"name": {"commands": [...], "default": bool}}
        self._rgb_default_name = ""       # default profile name
        self._load_rgb_profiles_data()
        # ─── Per-key RGB state ────────────────────────────────────────────
        self._perkey_dev_idx = 1          # keyboard is always device #1
        self._perkey_key_state: dict = {}  # key_name → (r, g, b)
        self._perkey_buttons: dict = {}    # key_name → KeyButton
        self._perkey_mappings = None       # last parsed mappings dict
        self._perkey_name_to_lids: dict = {}
        # Check G-MODE state at startup
        central = QWidget()
        self.setCentralWidget(central)
        root = QVBoxLayout(central)
        root.setSpacing(3)
        root.setContentsMargins(4, 4, 4, 3)

        self.tabs = QTabWidget()
        self.tabs.setFont(get_font(8, True))
        root.addWidget(self.tabs, stretch=1)

        self._build_tab_dashboard()
        self._build_tab_co()
        self._build_tab_corefreq_terminal()
        self._busy = False
        self.gaming_settings = {}

        # NVML — Startup perf fix B: nvmlInit()/nvmlDeviceGetHandleByIndex()
        # can wake a sleeping dGPU in hybrid-graphics mode, costing up to
        # ~2s. Start with "unknown" placeholders and initialize in the
        # background once the window is already visible (see
        # _kick_off_background_detection).
        self.nvml_available = False
        self.nvml_handle = None
        self._nvml = None

        self._build_tab_gpu()
        self._refresh_profile_list()
        self._build_tab_extra_tools()
        self._build_tab_rgb()            # ─── RGB Controls ───────────

        # Telemetry bar
        bot = QVBoxLayout()
        bot.setSpacing(2)
        self.tele = SL("[ TELEMETRY INITIALIZING... ]", bold=True, color=C_CYAN, size=8)
        self.tele.setStyleSheet(f"QLabel{{background-color:{C_BG2};padding:2px 6px;border-radius:3px;}}")
        self.tele.setFixedHeight(18)
        bot.addWidget(self.tele)

        btns = QHBoxLayout()
        btns.setSpacing(4)
        self.btn_save = QPushButton("💾 Save Power Profile Configuration")
        self.btn_save.setObjectName("save_button")
        self.btn_save.setFixedHeight(22)
        self.btn_save.clicked.connect(self._save)
        btns.addWidget(self.btn_save)
        bot.addLayout(btns)
        root.addLayout(bot)

        self.tele_thread = TelemetryWorker()
        self.tele_thread.data.connect(self._tele_update)
        self.tele_thread.error.connect(self._log)  # K5: artık sessizce yutulmuyor
        self.tele_thread.start()

        wrapper.start_tray_background()

        self._cppc_timer = QTimer(self)
        self._cppc_timer.setInterval(3000)
        self._cppc_timer.timeout.connect(self._fetch_cppc)
        # B4: the CPPC timer is stopped initially; starts after the tab connection

        self._co_live_timer = QTimer(self)
        self._co_live_timer.setInterval(1000)
        self._co_live_timer.timeout.connect(self._update_co_live)
        # The CO live timer is also stopped initially; starts on tab switch
        self._init_co_live_handles()   # hwmon discovery and handle opening

        # B3+B4: save tab indices and connect to the currentChanged signal
        self._connect_tab_timers()

        # ─── Auto-load the first profile (display only, don't apply) ──────────
        active_profile = None
        try:
            state_path = "/tmp/ryzenadj_active_profile.state"
            if os.path.exists(state_path):
                with open(state_path, "r") as f:
                    active_profile = f.read().strip()
        except Exception:
            pass

        if active_profile and active_profile in self.profiles:
            # Just display the active profile (don't apply)
            self._load_profile(active_profile, apply=False)
        elif self.profiles:
            # Show the first profile by default
            self._load_profile(self.profiles[0], apply=False)

        # Force-render the tab bar's selected highlight on the first event loop pass.
        # Qt sometimes skips the tab:selected pseudo-state on its first paint
        # for tab widgets created before window show(), so this is needed.
        QTimer.singleShot(0, lambda: self.tabs.tabBar().update())

        # Startup perf fix A+B: kick off the alienfx_cli device detection
        # and NVML init in the background, once the window is already
        # visible, instead of blocking __init__ on them.
        QTimer.singleShot(0, self._kick_off_background_detection)

#------------Yani METODLAR--------------------------------------

    def _on_profile_left_click(self, name):
        if self._busy:
            return
        if self.edit_mode:
            self._toggle_edit_mode(name)
        else:
            # If G-MODE is active and the clicked profile is performance, apply the gmode profile
            if name == "performance" and self.gmode_active:
                name = "gmode"
            self._load_and_apply_profile(name)

    def _on_profile_right_click(self, name):
        """Right-click: toggle edit mode."""
        self._toggle_edit_mode(name)

    def _toggle_edit_mode(self, name):
        """Right-click: toggle edit mode."""
        # If G-MODE is active and the clicked profile is performance, edit gmode
        if name == "performance" and self.gmode_active:
            name = "gmode"

        if self.edit_mode and self.edit_profile == name:
            # Right-click the same profile again -> close edit mode
            self.edit_mode = False
            self.edit_profile = None
            self._update_edit_labels()
            self._update_controls_state()
            # Show the active profile again (the most recently applied one)
            if self.current:
                self._load_profile(self.current, apply=False)
            return

        # Enter edit mode (or switch)
        self.edit_mode = True
        self.edit_profile = name
        self._load_profile(name, apply=False)  # Load only, don't apply
        self._update_edit_labels()
        self._update_controls_state()

    def _update_edit_labels(self):
        """Updates the [EDIT] marker on the profile buttons."""
        for btn_name, widgets in self.profile_buttons.items():
            edit_label = widgets.get("edit_label")
            if edit_label is None:
                continue

            # G-MODE aktifse performance butonu gmode'u temsil eder
            if btn_name == "performance" and self.gmode_active:
                actual_name = "gmode"
            else:
                actual_name = btn_name

            if self.edit_mode and self.edit_profile == actual_name:
                edit_label.setText(" [EDIT]")
                edit_label.setVisible(True)
            else:
                edit_label.setText("")
                edit_label.setVisible(False)

    def _update_controls_state(self):
        """Enables/disables all inputs based on edit_mode."""
        is_editable = self.edit_mode and self.current == self.edit_profile

        # Power Limits and Fan Boost
        for k, w in self.config.items():
            if hasattr(w, 'setEnabled'):
                w.setEnabled(is_editable)
                if is_editable:
                    w.set_grey(False)
                else:
                    w.set_grey(True)

        # Curve Optimizer - All Core
        if self._coall_entry:
            self._coall_entry.setEnabled(is_editable)
            self._coall_entry.set_grey(not is_editable)

        # Curve Optimizer - Per Core
        for cid, entry, lbl in self._co_core_wgts:
            entry.setEnabled(is_editable)
            entry.set_grey(not is_editable)

        # Update in a way that the _sync_coall call doesn't affect the entries
        self._sync_coall()

    def _refresh_gaming_status(self, elevated=True):
        """Read current values for all gaming settings and update UI.

        First does a plain, unprivileged local read (sysctl -n + direct
        file reads) — this covers everything except a few /sys/kernel/debug
        paths and never prompts for a password. If elevated=True and some
        values couldn't be read locally (because they need root), a second
        pass fetches just those via root_helper/pkexec. elevated=False skips
        that second pass entirely, so this can be called at startup without
        ever showing a password prompt.
        """
        # Update THP combos
        self._refresh_thp_status()

        if not hasattr(self, 'gaming_widgets') or not self.gaming_widgets:
            return

        local_values = {}
        needs_root_keys = []
        for key, widgets in self.gaming_widgets.items():
            info = widgets["info"]
            path = info["path"]
            try:
                if info["type"] == "sysctl":
                    sysctl_path = find_tool("sysctl")
                    if not sysctl_path:
                        local_values[key] = "(no sysctl)"
                        continue
                    result = subprocess.run([sysctl_path, "-n", path], capture_output=True, text=True)
                    if result.returncode == 0:
                        local_values[key] = result.stdout.strip()
                    else:
                        err = (result.stderr or "").strip()
                        local_values[key] = "(no sysctl)" if "unknown key" in err.lower() else "(?)"
                else:
                    if os.path.exists(path):
                        with open(path, "r") as f:
                            content = f.read().strip()
                        m = re.search(r"\[([^\]]+)\]", content)
                        local_values[key] = m.group(1) if m else (content or "(empty)")
                    else:
                        # Could genuinely be missing, or just unreadable
                        # without root (e.g. /sys/kernel/debug/*). We can't
                        # tell the difference without elevation.
                        local_values[key] = "(needs root)"
                        needs_root_keys.append(key)
            except PermissionError:
                local_values[key] = "(needs root)"
                needs_root_keys.append(key)
            except Exception as e:
                local_values[key] = f"(err: {e})"[:60]

        self._apply_gaming_status_values(local_values)

        if not elevated or not needs_root_keys:
            return

        # FIX: some paths (sched_min_base_slice, sched_migration_cost,
        # sched_nr_migrate under /sys/kernel/debug) require root just to
        # check whether the file exists. This second pass fetches only
        # those specific values via root_helper. It only runs when
        # explicitly requested (Refresh button / after Apply) — never
        # automatically at startup — so opening the app never triggers a
        # password prompt on its own.
        import threading
        settings_payload = {
            key: {"path": self.gaming_widgets[key]["info"]["path"], "type": self.gaming_widgets[key]["info"]["type"]}
            for key in needs_root_keys
        }

        def worker():
            values = {}
            err_msg = None
            try:
                json_arg = json.dumps({"op": "read_gaming_status", "settings": settings_payload})
                env = os.environ.copy()
                for var in ['DISPLAY', 'XAUTHORITY', 'XDG_RUNTIME_DIR', 'DBUS_SESSION_BUS_ADDRESS']:
                    if var in os.environ:
                        env[var] = os.environ[var]
                proc = subprocess.Popen(
                    ["pkexec", self.ROOT_HELPER_PATH],
                    stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                    text=True, env=env,
                )
                out, perr = proc.communicate(input=json_arg, timeout=30)
                if proc.returncode == 0:
                    res = self._parse_root_helper_output(out)
                    if res.get("ok"):
                        values = res.get("values", {})
                    else:
                        err_msg = res.get("error", "Unknown error")
                else:
                    err_msg = perr.strip() if perr else f"pkexec exited with code {proc.returncode} (auth cancelled/denied?)"
            except Exception as e:
                err_msg = str(e)
            if err_msg:
                QTimer.singleShot(0, self, lambda: self._log(f"⚠️ Could not read root-only gaming values: {err_msg}"))
            if values:
                QTimer.singleShot(0, self, lambda: self._apply_gaming_status_values(values))

        threading.Thread(target=worker, daemon=True).start()

    def _apply_gaming_status_values(self, values):
        """Updates the Gaming Optimizations labels for the given keys only
        (partial updates don't clobber values not included in this call)."""
        for key, value in values.items():
            widgets = self.gaming_widgets.get(key)
            if not widgets:
                continue
            info = widgets["info"]
            widgets["current_label"].setText(value)
            if value == info["recommended"]:
                widgets["current_label"].setStyleSheet("color: #8ec07c; font-size: 8pt;")
            else:
                widgets["current_label"].setStyleSheet("color: #fe8019; font-size: 8pt;")

    def _apply_selected_gaming(self):
        """
        Apply button: first ensure the required G-MODE state, then apply the gaming settings.
        """
        target_mode = self.gmode_combo.currentText()
        need_reload = False
        force = False

        if target_mode == "G-MODE" and not self.gmode_active:
            need_reload = True
            force = True
        elif target_mode == "OVERDRIVE" and self.gmode_active:
            need_reload = True
            force = False

        if need_reload:
            self.gaming_apply_btn.setEnabled(False)
            self._reload_alienware_wmi(
                force_gmode=force,
                callback=lambda success: self._on_mod_reload_done(success)
            )
        else:
            self._do_apply_gaming()

    def _apply_all_gaming(self):
        """Applies all gaming settings with the recommended values (ignores the checkboxes)."""
        if not hasattr(self, 'gaming_settings') or not self.gaming_settings:
            self._log("⚠️ Gaming settings not loaded yet.")
            return
        for widgets in self.gaming_widgets.values():
            widgets["checkbox"].setChecked(True)
        self._apply_gaming_settings(self.gaming_settings)

    def _apply_gaming_settings(self, settings_dict, thp_settings=None):
        """Apply gaming settings and THP settings in a single script."""
        script_lines = ["#!/usr/bin/env python3", "import os, sys, subprocess", ""]

        # Gaming settings
        for key, info in settings_dict.items():
            path = info["path"]
            value = info["recommended"]
            if info["type"] == "sysctl":
                script_lines.append(f"""
try:
    result = subprocess.run(['sysctl', '-w', '{path}={value}'], capture_output=True, text=True)
    if result.returncode != 0:
        print(f"WARNING: {{result.stderr.strip()}}", file=sys.stderr)
    else:
        print(result.stdout.strip())
except Exception as e:
    print(f"WARNING: Could not set {path}: {{e}}", file=sys.stderr)
""")
            else:
                # File write – first check existence and writability
                script_lines.append(f"""
try:
    if os.path.exists('{path}'):
        with open('{path}', 'w') as f:
            f.write('{value}')
        print(f"OK: {path} -> {value}")
    else:
        print(f"WARNING: {path} does not exist, skipping.", file=sys.stderr)
except Exception as e:
    print(f"WARNING: Could not write {path}: {{e}}", file=sys.stderr)
""")

        # THP settings (same check)
        if thp_settings:
            for name, thp in thp_settings.items():
                path = thp["path"]
                value = thp["value"]
                script_lines.append(f"""
try:
    if os.path.exists('{path}'):
        with open('{path}', 'w') as f:
            f.write('{value}')
        print(f"OK: {path} -> {value}")
    else:
        print(f"WARNING: {path} does not exist, skipping.", file=sys.stderr)
except Exception as e:
    print(f"WARNING: Could not write {path}: {{e}}", file=sys.stderr)
""")

        # PCI latency settings (no change)
        pci_commands = [
            "setpci -v -s '*:*' latency_timer=20",
            "setpci -v -s '0:0' latency_timer=0",
            "setpci -v -d '*:*:04xx' latency_timer=80",
        ]
        for cmd in pci_commands:
            script_lines.append(f"""
try:
    result = subprocess.run("{cmd}", shell=True, capture_output=True, text=True)
    if result.returncode != 0:
        print(f"WARNING: {{result.stderr.strip()}}", file=sys.stderr)
    else:
        print(result.stdout.strip())
except Exception as e:
    print(f"WARNING: Could not run '{cmd}': {{e}}", file=sys.stderr)
""")

        script_lines.append("print('All settings applied (warnings may have occurred).')")
        script_content = "\n".join(script_lines)

        # K3: root_helper.py'nin "run_script" op'u artık yalnızca
        # ALLOWED_SCRIPTS_DIR altında, root sahipli script'leri kabul ediyor
        # (bkz. root_helper.py K3 notu). GUI tarafından /tmp'ye yazılan bu
        # geçici, kullanıcı sahipli script o kısıtları geçemez. Bunun
        # yerine script *içeriğini* doğrudan "run_script_content" op'una
        # gönderiyoruz; dosya yalnızca zaten root olan root_helper süreci
        # tarafından, kendi kontrolündeki dizinde oluşturulup çalıştırılıyor.
        self._run_root_helper_command(
            {"op": "run_script_content", "content": script_content},
            "All settings applied (warnings may have occurred).",
            "Failed to apply settings.",
            callback=lambda out: self._on_gaming_applied(out, None)
        )

    def _on_gaming_applied(self, output, script_path):
        if not script_path:
            self._log("✅ Gaming optimizations applied.")
            self._refresh_gaming_status()
            return
        try:
            os.remove(script_path)
        except Exception:
            pass
        self._log("✅ Gaming optimizations applied.")
        self._refresh_gaming_status()

    def _restore_gaming_defaults(self):
        """Resets THP and gaming settings to defaults."""
        if not self.gmode_active:
            self._log("⚠️ G-MODE is not active, defaults cannot be restored.")
            return

        # THP default
        self.thp_enabled_combo.setCurrentText("madvise")
        self.thp_defrag_combo.setCurrentText("madvise")
        self.thp_shmem_combo.setCurrentText("never")
        # Set the gaming checkboxes to the recommended values (check all)
        for widgets in self.gaming_widgets.values():
            widgets["checkbox"].setChecked(True)
        # G-MODE'u kapat (comboyu OVERDRIVE yap) – bu _disable_gmode'u tetikleyecek
        if self.gmode_combo.currentText() != "OVERDRIVE":
            self.gmode_combo.setCurrentText("OVERDRIVE")
        # REMOVE the _do_apply_gaming call, since the combo change will already do the reset
        self._log("✅ Defaults restored.")

    def _refresh_thp_status(self):
        """Update THP combos with current system values."""
        import os, re
        self.thp_enabled_combo.blockSignals(True)
        self.thp_defrag_combo.blockSignals(True)
        self.thp_shmem_combo.blockSignals(True)

        try:
            # enabled
            path = "/sys/kernel/mm/transparent_hugepage/enabled"
            if os.path.exists(path):
                with open(path, "r") as f:
                    content = f.read().strip()
                    match = re.search(r"\[([^\]]+)\]", content)
                    value = match.group(1) if match else content
                idx = self.thp_enabled_combo.findText(value)
                if idx >= 0:
                    self.thp_enabled_combo.setCurrentIndex(idx)

            # defrag
            path = "/sys/kernel/mm/transparent_hugepage/defrag"
            if os.path.exists(path):
                with open(path, "r") as f:
                    content = f.read().strip()
                    match = re.search(r"\[([^\]]+)\]", content)
                    value = match.group(1) if match else content
                idx = self.thp_defrag_combo.findText(value)
                if idx >= 0:
                    self.thp_defrag_combo.setCurrentIndex(idx)

            # shmem_enabled
            path = "/sys/kernel/mm/transparent_hugepage/shmem_enabled"
            if os.path.exists(path):
                with open(path, "r") as f:
                    content = f.read().strip()
                    match = re.search(r"\[([^\]]+)\]", content)
                    value = match.group(1) if match else content
                idx = self.thp_shmem_combo.findText(value)
                if idx >= 0:
                    self.thp_shmem_combo.setCurrentIndex(idx)
        except Exception:
            pass

        self.thp_enabled_combo.blockSignals(False)
        self.thp_defrag_combo.blockSignals(False)
        self.thp_shmem_combo.blockSignals(False)

    def _on_thp_applied(self, output, script_path):
        try:
            os.remove(script_path)
        except Exception:
            pass
        self._refresh_thp_status()
        self._log("✅ THP settings updated.")

    def _update_gmode_combo_from_module(self):
        """
        Updates the combo based on the module state.
        
        ✅ FIX: Reads the ACTUAL running G-Mode state from sysfs, not just
                     the config file. Shows the runtime state.
        """
        # 1. Check whether the module is loaded
        module_loaded = False
        try:
            with open("/proc/modules", "r") as f:
                for line in f:
                    if line.startswith("alienware_wmi"):
                        module_loaded = True
                        break
        except Exception:
            pass

        if not module_loaded:
            self.gmode_combo.setEnabled(False)
            self.gmode_combo.setCurrentText("OVERDRIVE")
            self.gmode_active = False
            self._log("⚠️ alienware_wmi module not loaded. G-MODE feature disabled.")
            return

        # 2. Read the ACTUAL G-MODE state from sysfs (RUNTIME STATE - IMPORTANT!)
        # Priority order:
        #   a) /sys/module/alienware_wmi/parameters/force_gmode (module runtime param)
        #   b) /sys/devices/platform/alienware-wmi/platform_profile (HW profili)
        #   c) /etc/modprobe.d/alienware-wmi.conf (config file fallback)
        
        force_gmode = False
        
        # 2a. Check the module parameter (MOST IMPORTANT - RUNNING STATE)
        try:
            param_file = Path("/sys/module/alienware_wmi/parameters/force_gmode")
            if param_file.exists():
                content = param_file.read_text().strip()
                force_gmode = content.lower() in ("1", "y", "true", "on")
                # Debug log
                # self._log(f"[DEBUG] /sys/module/alienware_wmi/parameters/force_gmode = {content}")
        except Exception as e:
            pass
        
        # 2b. If the param isn't found, check the platform_profile file
        if not force_gmode:
            try:
                profile_file = Path("/sys/devices/platform/alienware-wmi/platform_profile")
                if profile_file.exists():
                    content = profile_file.read_text().strip().lower()
                    # Check whether the profile is "gmode" or similar
                    force_gmode = "gmode" in content or (
                        "performance" in content and "gaming" in content
                    )
                    # Debug log
                    # self._log(f"[DEBUG] platform_profile = {content}")
            except Exception as e:
                pass
        
        # 2c. Fallback: read from the config file (the setting at system startup)
        #     Only used if the sysfs files don't exist
        if not force_gmode:
            try:
                conf_file = Path("/etc/modprobe.d/alienware-wmi.conf")
                if conf_file.exists():
                    content = conf_file.read_text()
                    force_gmode = "force_gmode=true" in content
                    # Debug log
                    # self._log(f"[DEBUG] /etc/modprobe.d/alienware-wmi.conf force_gmode = {force_gmode}")
            except Exception as e:
                pass

        # 3. Update the combo and state
        if force_gmode:
            self.gmode_active = True
            self.gmode_combo.setCurrentText("G-MODE")
        else:
            self.gmode_active = False
            self.gmode_combo.setCurrentText("OVERDRIVE")

        self._update_profile_buttons()
        self._update_extra_tools_state()

    def _on_gmode_combo_changed(self, index):
        """Enable/disable G-MODE when the combo changes."""
        if not self.gmode_combo.isEnabled():
            return
        if self.gmode_combo.currentText() == "G-MODE":
            self._enable_gmode()
        else:
            self._disable_gmode()

    def _enable_gmode(self):
        """Enable G-MODE: reload the module + create the profile."""
        if self.gmode_active:
            return
        if self.gmode_process is not None:
            self._log("⚠️ An operation is already in progress, please wait.")
            return
        self.gmode_combo.setEnabled(False)
        self._reload_alienware_wmi(force_gmode=True, callback=self._on_gmode_enabled)

    def _disable_gmode(self):
        """G-MODE'u deaktif et."""
        if not self.gmode_active:
            return
        if self.gmode_process is not None:
            self._log("⚠️ An operation is already in progress, please wait.")
            return
        self.gmode_combo.setEnabled(False)
        self._reload_alienware_wmi(force_gmode=False, callback=self._on_gmode_disabled)

    def _on_gmode_enabled(self, success):
        self.gmode_combo.setEnabled(True)
        if success:
            self.gmode_active = True
            self._update_extra_tools_state()
            self._update_profile_buttons()
            self._log("✅ G-MODE enabled.")
            try:
                perf_data = wrapper.load_profile("performance")
                if perf_data:
                    # PROFILES_DIR artık root sahipli (/etc/ryzenadj-gui/profiles);
                    # doğrudan open(...,"w") yerine root_helper'ın
                    # save_power_profile op'u üzerinden yazıyoruz.
                    self._run_root_helper_command(
                        {"op": "save_power_profile", "name": "gmode",
                         "content": json.dumps(perf_data, indent=2)},
                        "G-MODE profile created (base: performance).",
                        "G-MODE profile creation error",
                    )
            except Exception as e:
                self._log(f"⚠️ G-MODE profile creation error: {e}")
        else:
            self.gmode_combo.blockSignals(True)
            self.gmode_combo.setCurrentText("OVERDRIVE")
            self.gmode_combo.blockSignals(False)
            self._log("❌ G-MODE enable failed.")

    def _on_gmode_disabled(self, success):
        self.gmode_combo.setEnabled(True)
        self.gmode_active = False
        if success:
            self._update_extra_tools_state()
            self._update_profile_buttons()
            self._reset_gaming_settings()
            self._log("✅ G-MODE disabled.")
        else:
            self.gmode_combo.blockSignals(True)
            self.gmode_combo.setCurrentText("OVERDRIVE")
            self.gmode_combo.blockSignals(False)
            self._log("❌ G-MODE disable failed, but mode set to OVERDRIVE.")

    ROOT_HELPER_PATH = "/usr/local/lib/ryzenadj-gui/root_helper.py"

    def _reload_alienware_wmi(self, force_gmode, callback):
        """Unloads and reloads the Alienware-WMI module."""
        payload = json.dumps({"op": "reload_alienware_wmi", "force_gmode": bool(force_gmode)})
        env = os.environ.copy()
        for var in ['DISPLAY', 'XAUTHORITY', 'XDG_RUNTIME_DIR', 'DBUS_SESSION_BUS_ADDRESS']:
            if var in os.environ:
                env[var] = os.environ[var]

        try:
            self.gmode_process = subprocess.Popen(
                ["pkexec", self.ROOT_HELPER_PATH],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                env=env,
            )
            out, err = self.gmode_process.communicate(payload, timeout=15)
            if self.gmode_process.returncode == 0:
                self._log("✅ Alienware-WMI reloaded successfully.")
                if out:
                    self._log(out.strip())
                callback(True)
            else:
                self._log(f"❌ Alienware-WMI reload failed (exit: {self.gmode_process.returncode})")
                if err:
                    self._log(f"stderr: {err.strip()}")
                callback(False)
        except subprocess.TimeoutExpired:
            if self.gmode_process:
                self.gmode_process.kill()
            self._log("❌ Alienware-WMI reload timed out.")
            callback(False)
        except Exception as e:
            self._log(f"❌ Alienware-WMI reload error: {e}")
            callback(False)
        finally:
            self.gmode_process = None

    def _load_extra_settings(self):
        """Reads from the extra settings file and reflects it into the GUI."""
        if self.extra_settings_path.exists():
            try:
                with open(self.extra_settings_path, "r") as f:
                    self.extra_settings = json.load(f)
            except Exception as e:
                self._log(f"Failed to load extra settings: {e}")
                self.extra_settings = {}
        else:
            self.extra_settings = {}

        # Fill in default values (if the file is missing or incomplete)
        if not self.extra_settings.get("thp"):
            self.extra_settings["thp"] = {
                "enabled": "madvise",
                "defrag": "madvise",
                "shmem": "never"
            }
        if not self.extra_settings.get("gaming"):
            self.extra_settings["gaming"] = {}

        # Update GUI elements (THP)
        thp = self.extra_settings.get("thp", {})
        if "enabled" in thp:
            idx = self.thp_enabled_combo.findText(thp["enabled"])
            if idx >= 0:
                self.thp_enabled_combo.setCurrentIndex(idx)
        if "defrag" in thp:
            idx = self.thp_defrag_combo.findText(thp["defrag"])
            if idx >= 0:
                self.thp_defrag_combo.setCurrentIndex(idx)
        if "shmem" in thp:
            idx = self.thp_shmem_combo.findText(thp["shmem"])
            if idx >= 0:
                self.thp_shmem_combo.setCurrentIndex(idx)

        # Gaming checkboxes (only if saved values exist)
        gaming = self.extra_settings.get("gaming", {})
        for key, value in gaming.items():
            if key in self.gaming_widgets:
                self.gaming_widgets[key]["checkbox"].setChecked(value is not None and value != "")

        # Update the scripts (only if extra settings are populated)
        if self.extra_settings:
            self._update_scripts_with_extra(self.extra_settings)

    def _save_extra_settings(self):
        """Writes the current GUI values to the extra settings file and updates the scripts."""
        data = {
            "thp": {
                "enabled": self.thp_enabled_combo.currentText(),
                "defrag": self.thp_defrag_combo.currentText(),
                "shmem": self.thp_shmem_combo.currentText()
            },
            "gaming": {}
        }
        for key, widgets in self.gaming_widgets.items():
            if widgets["checkbox"].isChecked():
                data["gaming"][key] = widgets["info"]["recommended"]
            else:
                data["gaming"][key] = None

        self.extra_settings = data
        try:
            self.extra_settings_path.parent.mkdir(parents=True, exist_ok=True)
            with open(self.extra_settings_path, "w") as f:
                json.dump(data, f, indent=2)
            self._update_scripts_with_extra(data)
            # Leave a single, clear log line:
            self._log("✅ Extra settings saved and profiles updated.")
        except Exception as e:
            self._log(f"Extra settings kaydedilemedi: {e}")

    def _update_scripts_with_extra(self, extra_data):
        """Adds the extra settings into the gmode and custom JSON files and scripts."""
        for profile_name in ["gmode", "custom"]:
            try:
                try:
                    cfg = wrapper.load_profile(profile_name)
                except FileNotFoundError:
                    cfg = wrapper.load_profile("performance")
                if cfg:
                    cfg["extra"] = extra_data
                    # PROFILES_DIR artık root sahipli; doğrudan open(...,"w")
                    # yerine root_helper'ın save_power_profile op'unu kullan.
                    self._run_root_helper_command(
                        {"op": "save_power_profile", "name": profile_name,
                         "content": json.dumps(cfg, indent=2)},
                        f"{profile_name} profile updated with extra settings.",
                        f"{profile_name} could not be updated",
                    )
                    wrapper.write_shell_script(profile_name, cfg)
                    # Duplicate success logs were removed from here.
            except Exception as e:
                self._log(f"⚠️ {profile_name} could not be updated: {e}")

    def _apply_extra_settings(self):
        """Applies the saved extra settings to the system and updates the scripts."""
        # Update the scripts first (so extra settings are applied automatically on subsequent profile changes)
        self._update_scripts_with_extra(self.extra_settings)
        # Then apply it to the current system
        self._apply_gaming_settings(
            {key: self.gaming_widgets[key]["info"] for key, val in self.extra_settings.get("gaming", {}).items() if val},
            {
                "thp_enabled": {"path": "/sys/kernel/mm/transparent_hugepage/enabled", "value": self.extra_settings.get("thp", {}).get("enabled", "madvise")},
                "thp_defrag": {"path": "/sys/kernel/mm/transparent_hugepage/defrag", "value": self.extra_settings.get("thp", {}).get("defrag", "madvise")},
                "thp_shmem": {"path": "/sys/kernel/mm/transparent_hugepage/shmem_enabled", "value": self.extra_settings.get("thp", {}).get("shmem", "never")},
            }
        )
        self._log("✅ Extra settings applied and scripts updated.")

    def _clear_layout(self, layout):
        if layout is None:
            return
        while layout.count():
            item = layout.takeAt(0)
            if not item:
                continue
            w = item.widget()
            if w:
                w.deleteLater()
            else:
                sub_layout = item.layout()
                if sub_layout is not None:
                    self._clear_layout(sub_layout)

    def _mark(self):
        """Marks that a change has been made to the profiles."""
        self.dirty = True

    def _update_extra_tools_state(self):
        """Enables/disables the controls on the Extra Tools page based on the G-MODE state."""
        # If it's OVERDRIVE (gmode_active False), disable all controls, only keep the combo active.
        enabled = self.gmode_active

        # THP combos
        self.thp_enabled_combo.setEnabled(enabled)
        self.thp_defrag_combo.setEnabled(enabled)
        self.thp_shmem_combo.setEnabled(enabled)

        # Gaming checkboxes
        for widgets in self.gaming_widgets.values():
            widgets["checkbox"].setEnabled(enabled)

        # Butonlar
        self.gaming_refresh_btn.setEnabled(enabled)
        self.gaming_save_btn.setEnabled(enabled)
        self.gaming_restore_btn.setEnabled(enabled)

        # The combo is always active
        self.gmode_combo.setEnabled(True)

    def _reset_gaming_settings(self):
        """Resets the gaming settings (null), updates the scripts."""
        # Set the gaming values inside extra_settings to None
        if "gaming" in self.extra_settings:
            for key in self.extra_settings["gaming"]:
                self.extra_settings["gaming"][key] = None
        # Update the THP combos with the current system values (don't change the system)
        self._refresh_thp_status()
        # Deselect all checkboxes
        for widgets in self.gaming_widgets.values():
            widgets["checkbox"].setChecked(False)
        # Save extra_settings and update the scripts
        self._save_extra_settings()
        self._log("✅ Gaming settings reset in profiles and scripts.")

    def _do_apply_gaming(self):
        """Applies only the gaming optimizations and THP settings."""
        selected = {}
        for key, widgets in self.gaming_widgets.items():
            if widgets["checkbox"].isChecked():
                selected[key] = widgets["info"]

        thp_settings = {
            "thp_enabled": {"path": "/sys/kernel/mm/transparent_hugepage/enabled", "value": self.thp_enabled_combo.currentText()},
            "thp_defrag": {"path": "/sys/kernel/mm/transparent_hugepage/defrag", "value": self.thp_defrag_combo.currentText()},
            "thp_shmem": {"path": "/sys/kernel/mm/transparent_hugepage/shmem_enabled", "value": self.thp_shmem_combo.currentText()},
        }

        if not selected and not thp_settings:
            self._log("❌ No option to apply.")
            return

        # This call already prints its own "✅ Extra settings saved..." log
        self._save_extra_settings()

        # The duplicate log below was removed:
        # self._log("✅ Extra settings saved and scripts updated.")

        selected_info = {key: widgets["info"] for key, widgets in self.gaming_widgets.items() if widgets["checkbox"].isChecked()}
        self._apply_gaming_settings(selected_info, thp_settings)

    def _save_gaming_settings(self):
        """Save current Custom / G-MODE settings (THP + Gaming) to JSON and scripts."""
        if not self.gmode_active:
            self._log("⚠️ G-MODE is not active, settings cannot be saved.")
            return
        # Gather current UI state
        data = {
            "thp": {
                "enabled": self.thp_enabled_combo.currentText(),
                "defrag": self.thp_defrag_combo.currentText(),
                "shmem": self.thp_shmem_combo.currentText()
            },
            "gaming": {}
        }
        for key, widgets in self.gaming_widgets.items():
            if widgets["checkbox"].isChecked():
                data["gaming"][key] = widgets["info"]["recommended"]
            else:
                data["gaming"][key] = None

        self.extra_settings = data
        # Save to file
        try:
            self.extra_settings_path.parent.mkdir(parents=True, exist_ok=True)
            with open(self.extra_settings_path, "w") as f:
                json.dump(data, f, indent=2)
            self._log("✅ Extra settings saved to file.")
        except Exception as e:
            self._log(f"❌ Extra settings kaydedilemedi: {e}")
            return
        # Update gmode and custom scripts with this extra data
        self._update_scripts_with_extra(data)
        self._log("✅ G-MODE / Custom scripts updated with saved settings.")

    def _execute_via_root_helper(self, payload_dict):
        """
        Runs all root operations centrally through root_helper.py, to
        trigger the Polkit cache (auth_admin_keep).
        """
        try:
            payload = json.dumps(payload_dict)
            process = subprocess.Popen(
                ["pkexec", self.ROOT_HELPER_PATH],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
            out, err = process.communicate(payload, timeout=15)
            return process.returncode == 0, out, err
        except subprocess.TimeoutExpired:
            return False, "", "Operation timed out (Timeout)."
        except Exception as e:
            return False, "", str(e)

    def _parse_root_helper_output(self, out):
        """root_helper.py always prints its JSON result as the LAST line of
        stdout. If anything upstream (a library, a subprocess it shells out
        to, a stray print somewhere) ever leaks extra text onto stdout, a
        naive json.loads(out) on the whole blob breaks. Parsing only the
        last non-empty line makes this robust to that class of bug."""
        if not out:
            raise json.JSONDecodeError("empty output", "", 0)
        lines = [l for l in out.splitlines() if l.strip()]
        if not lines:
            raise json.JSONDecodeError("empty output", "", 0)
        return json.loads(lines[-1])

    def _run_root_helper_command(self, payload_dict, success_msg, fail_msg, callback=None):
        import threading
        def worker():
            proc = None   # D2: pre-defined to avoid a NameError risk
            try:
                json_arg = json.dumps(payload_dict)
                # Copy environment variables and add the required ones
                env = os.environ.copy()
                for var in ['DISPLAY', 'XAUTHORITY', 'XDG_RUNTIME_DIR', 'DBUS_SESSION_BUS_ADDRESS']:
                    if var in os.environ:
                        env[var] = os.environ[var]

                # FIX: root_helper.py only reads JSON from stdin.
                # Pass root_helper.py directly to pkexec (not the interpreter) —
                # this triggers the same polkit action as G-MODE, and the auth cache
                # is shared across all operations.
                proc = subprocess.Popen(
                    ["pkexec", self.ROOT_HELPER_PATH],
                    stdin=subprocess.PIPE,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                    env=env,
                )
                out, err = proc.communicate(input=json_arg, timeout=120)

                if proc.returncode == 0:
                    try:
                        res = self._parse_root_helper_output(out)
                        if res.get("ok"):
                            msg = res.get("message", "")
                            if msg:
                                for line in msg.strip().splitlines():
                                    self.log_signal.emit(line)
                            self.log_signal.emit(f"✔ {success_msg}")
                            if callback:
                                return_val = "OK\n" if payload_dict.get("op") == "save_power_profile" else ""
                                QTimer.singleShot(0, self, lambda: callback(return_val))
                            return
                        else:
                            self.log_signal.emit(f"✘ {fail_msg}: {res.get('error', 'Unknown Error')}")
                            # A6: the callback wasn't being called in the ok==False branch → UI would hang forever
                            if callback:
                                QTimer.singleShot(0, self, lambda: callback(""))
                    except json.JSONDecodeError:
                        self.log_signal.emit(f"✘ {fail_msg}: Invalid root_helper response.")
                        if callback:
                            QTimer.singleShot(0, self, lambda: callback(""))
                else:
                    # FIX: root_helper.py intentionally returns exit code 1 on
                    # "ok": False, but it still writes the real error message
                    # as JSON to stdout. This branch used to never read stdout,
                    # so it showed a fake "Permission denied" message instead
                    # of the real cause.
                    real_err = None
                    if out:
                        try:
                            res = self._parse_root_helper_output(out)
                            real_err = res.get("error")
                        except json.JSONDecodeError:
                            pass
                    if real_err:
                        self.log_signal.emit(f"✘ {fail_msg}: {real_err}")
                    else:
                        self.log_signal.emit(f"✘ {fail_msg}: {err.strip() if err else 'pkexec/polkit authorization denied.'}")
                    # Same as A6: the callback wasn't being called in this branch either, UI could hang.
                    if callback:
                        QTimer.singleShot(0, self, lambda: callback(""))
            except subprocess.TimeoutExpired:
                if proc:   # D2: proc may still be None if Popen itself failed
                    proc.kill()
                    proc.wait()
                self.log_signal.emit(f"✘ {fail_msg}: timeout (120s)")
                if callback:
                    QTimer.singleShot(0, self, lambda: callback(""))
            except Exception as e:
                self.log_signal.emit(f"✘ {fail_msg}: {e}")
                # O3: Bu son except bloğunda callback hiç çağrılmıyordu.
                # Popen öncesi bir hata olursa (örn. env kurulumu) UI
                # sonsuza kadar "⏳" durumunda kalıyordu.
                if callback:
                    QTimer.singleShot(0, self, lambda: callback(""))

        threading.Thread(target=worker, daemon=True).start()

    # ─── TAB 1: DASHBOARD ──────────────────────────────────────────────
    def _build_tab_dashboard(self):
        tab = QWidget()
        tab.setStyleSheet(f"background:{C_BG};")
        hl = QHBoxLayout(tab)
        hl.setSpacing(0)
        hl.setContentsMargins(0, 0, 0, 0)

        sp = QSplitter(Qt.Horizontal)
        sp.setChildrenCollapsible(False)

        # ─── LEFT PANEL ────────────────────────────────────────────────
        lw = QWidget()
        lw.setStyleSheet(f"background:{C_BG};")
        ll = QVBoxLayout(lw)
        ll.setSpacing(6)
        ll.setContentsMargins(4, 4, 4, 4)

        # System Information
        g_si = QGroupBox(" System Information ")
        vsi = QVBoxLayout(g_si)
        vsi.setSpacing(3)
        vsi.setContentsMargins(8, 6, 8, 6)

        # C1: The window opens IMMEDIATELY with placeholder labels;
        # read_sys_info() runs on a background thread and updates the labels when done.
        def irow(k, v, vc=C_GREY, ks=62):
            h = QHBoxLayout()
            h.setSpacing(4)
            lk = SL(k, color=C_DGREY, size=8, selectable=True)
            lk.setFixedWidth(ks)
            lv = SL(v, color=vc, size=8, selectable=True)
            lv.setWordWrap(True)
            h.addWidget(lk)
            h.addWidget(lv, stretch=1)
            return h, lv  # return lv so we can update it asynchronously

        (rl_distro, lv_distro) = irow("Distro:", "…", C_BLUE)
        vsi.addLayout(rl_distro)
        (rl_kernel, lv_kernel) = irow("Kernel:", "…", C_LIME)
        vsi.addLayout(rl_kernel)
        vsi.addWidget(hsep())
        (rl_cpu1, lv_cpu1) = irow("CPU:", "…", C_YELLOW)
        vsi.addLayout(rl_cpu1)
        # CPU row 2 (for long model names)
        h2 = QHBoxLayout(); h2.setSpacing(4)
        sp2 = SL("", size=8, selectable=True); sp2.setFixedWidth(62)
        lv_cpu2 = SL("", color=C_YELLOW, size=8, selectable=True)
        h2.addWidget(sp2); h2.addWidget(lv_cpu2, stretch=1)
        vsi.addLayout(h2)
        (rl_cores, lv_cores) = irow("Cores:", "…", C_ORANGE)
        vsi.addLayout(rl_cores)
        vsi.addWidget(hsep())
        (rl_gpu0, lv_gpu0) = irow("GPU 0:", "…", C_BLUE)
        vsi.addLayout(rl_gpu0)
        (rl_gpu1, lv_gpu1) = irow("GPU 1:", "…", C_BLUE)
        vsi.addLayout(rl_gpu1)
        vsi.addWidget(hsep())
        # Dynamic rows for RAM (placeholder)
        self._ram_vbox = QVBoxLayout()
        self._ram_vbox.setSpacing(0); self._ram_vbox.setContentsMargins(0,0,0,0)
        (rl_ram0, lv_ram0) = irow("RAM:", "…", C_PURPLE)
        self._ram_vbox.addLayout(rl_ram0)
        vsi.addLayout(self._ram_vbox)

        ll.addWidget(g_si, stretch=1)

        # Async sysinfo doldurma
        def _on_sysinfo(sinfo):
            lv_distro.setText(sinfo.get("distro", "?"))
            lv_kernel.setText(sinfo.get("kernel", "?"))
            cpu = sinfo.get("cpu", "Unknown CPU")
            c1  = cpu[:46] if len(cpu) > 46 else cpu
            c2  = cpu[46:92] if len(cpu) > 46 else ""
            lv_cpu1.setText(c1)
            lv_cpu2.setText(c2)
            if sinfo.get("cores"):
                lv_cores.setText(f"{sinfo['cores']}C / {sinfo['threads']}T")
            gpus = sinfo.get("gpus", [])
            lv_gpu0.setText(gpus[0] if gpus else "Not found")
            lv_gpu0.setStyleSheet(f"color:{C_BLUE if gpus else C_VDGREY};")
            lv_gpu1.setText(gpus[1] if len(gpus) > 1 else "Disabled / Not found")
            lv_gpu1.setStyleSheet(f"color:{C_BLUE if len(gpus) > 1 else C_VDGREY};")
            # Rebuild the RAM rows
            while self._ram_vbox.count():
                item = self._ram_vbox.takeAt(0)
                w = item.widget()
                if w: w.deleteLater()
                elif item.layout(): self._clear_layout(item.layout())
            ram_lines = sinfo.get("ram_lines", [])
            for i, line in enumerate(ram_lines):
                (rl, _lv) = irow("RAM:" if i == 0 else "", line, C_PURPLE)
                self._ram_vbox.addLayout(rl)

        self._sysinfo_worker = SysInfoWorker()
        self._sysinfo_worker.ready.connect(_on_sysinfo)
        self._sysinfo_worker.start()

        # Terminal Logs
        self.g_term = QGroupBox(" Terminal Logs ")
        vt = QVBoxLayout(self.g_term)
        vt.setContentsMargins(6, 10, 6, 6)
        vt.setSpacing(2)
        self.log_box = QTextEdit()
        self.log_box.setReadOnly(True)
        self.log_box.setFont(get_font(8))
        self.log_box.setStyleSheet(f"background: {C_BG3}; border: 1px solid {C_BORDER}; color: #ebdbb2;")
        vt.addWidget(self.log_box)
        ll.addWidget(self.g_term, stretch=1)

        sp.addWidget(lw)

        # ─── RIGHT PANEL ───────────────────────────────────────────────
        rw = QWidget()
        rw.setStyleSheet(f"background:{C_BG};")
        rl_main = QVBoxLayout(rw)
        rl_main.setSpacing(6)
        rl_main.setContentsMargins(4, 4, 4, 4)

        rl_top = QHBoxLayout()
        rl_top.setSpacing(6)
        rl_top.setContentsMargins(0, 0, 0, 0)

        self.dynamic_widget = QWidget()
        self.dynamic_widget.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.s_layout = QVBoxLayout(self.dynamic_widget)
        self.s_layout.setSpacing(4)
        self.s_layout.setContentsMargins(0, 0, 0, 0)
        rl_top.addWidget(self.dynamic_widget, stretch=1)

        # Core Usage
        self.g_cpu_applet = QGroupBox(" Core Usage ")
        self.g_cpu_applet.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.v_cpu = QVBoxLayout(self.g_cpu_applet)
        self.v_cpu.setSpacing(3)
        self.v_cpu.setContentsMargins(8, 5, 8, 5)

        self.lbl_driver = SL("Drv: —", color=C_BLUE, size=8)
        self.lbl_gov = SL("Gov: —", color=C_GREEN, size=8)
        self.lbl_epp = SL("EPP: —", color=C_YELLOW, size=8)
        self.lbl_power = SL("CPU Pwr: - W", color=C_CYAN, size=8)
        self.lbl_freq = SL("Avg Frq: - MHz", color=C_YELLOW, size=8)
        top3 = QHBoxLayout()
        top3.setSpacing(14)
        top3.addWidget(self.lbl_driver)
        top3.addWidget(self.lbl_gov)
        top3.addWidget(self.lbl_epp)
        top3.addStretch()
        top3.addWidget(self.lbl_power)
        top3.addWidget(self.lbl_freq)
        self.v_cpu.addLayout(top3)
        self.v_cpu.addWidget(hsep())

        hdr_row = QHBoxLayout()
        hdr_row.setSpacing(0)
        for txt, w in [("Core", 38), ("Usage  ", 1), ("Core", 38), ("  Usage", 1)]:
            lb = SL(txt, bold=True, color=C_DGREY, size=8)
            if w == 1:
                lb.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
            else:
                lb.setFixedWidth(w)
            hdr_row.addWidget(lb, 1 if w == 1 else 0)
        self.v_cpu.addLayout(hdr_row)

        self.w_threads = QWidget()
        self.w_threads.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.grid_threads = QGridLayout(self.w_threads)
        self.grid_threads.setSpacing(2)
        self.grid_threads.setContentsMargins(0, 2, 0, 2)
        self.grid_threads.setColumnMinimumWidth(0, 38)
        self.grid_threads.setColumnMinimumWidth(2, 38)
        self.grid_threads.setColumnStretch(1, 1)
        self.grid_threads.setColumnStretch(3, 1)

        self.thread_bars = []
        for i in range(24):
            if i < 12:
                grid_row, col_id, col_bar = i, 0, 1
            else:
                grid_row, col_id, col_bar = i - 12, 2, 3

            lbl_id = SL(f"P{i:02d}", bold=True, color=C_DGREY, size=8)
            lbl_id.setFixedWidth(38)

            bar_w = QWidget()
            bar_l = QHBoxLayout(bar_w)
            bar_l.setSpacing(3)
            bar_l.setContentsMargins(0, 0, 0, 0)
            bar = QProgressBar()
            bar.setFixedHeight(10)
            bar.setTextVisible(False)
            bar.setRange(0, 100)
            bar.setValue(0)
            bar.setStyleSheet(
                f"QProgressBar{{border:1px solid {C_BORDER};background:{C_BG3};border-radius:2px;}}"
                f"QProgressBar::chunk{{background:#ffffff;border-radius:2px;}}"
            )
            lbl_pct = SL(" 0%", color="#ffffff", size=7)
            lbl_pct.setFixedWidth(28)
            bar_l.addWidget(bar, stretch=1)
            bar_l.addWidget(lbl_pct)

            self.grid_threads.addWidget(lbl_id, grid_row, col_id, Qt.AlignVCenter)
            self.grid_threads.addWidget(bar_w, grid_row, col_bar, Qt.AlignVCenter)
            self.thread_bars.append((lbl_id, lbl_pct, bar))

        self.v_cpu.addWidget(self.w_threads, stretch=1)
        rl_top.addWidget(self.g_cpu_applet, stretch=2)
        rl_main.addLayout(rl_top, stretch=1)

        # ─── POWER PROFILES (BUTTONS) ──────────────────────────────────
        g_prof = QGroupBox(" Power Profiles ")
        g_prof.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)

        vp = QHBoxLayout(g_prof)
        vp.setContentsMargins(10, 12, 10, 10)
        vp.setSpacing(10)

        self.PROFILE_STYLES = {
            "quiet": {"color": C_GREEN, "label": "QUIET"},
            "cool": {"color": C_CYAN, "label": "COOL"},
            "balanced": {"color": C_YELLOW, "label": "BALANCED"},
            "balanced-performance": {"color": C_ORANGE, "label": "PERFORMANCE"},
            "performance": {"color": C_STOP, "label": "OVERDRIVE"},
            "overdrive": {"color": C_WHITE, "label": "G-MODE"},
            "custom": {"color": C_PURPLE, "label": "CUSTOM"}
        }

        self.profile_buttons = {}
        vp.addStretch()

        for p_name in self.profiles:
            p_key = p_name.lower()
            info = self.PROFILE_STYLES.get(p_key, {"color": C_GREY, "label": p_name.upper()})
            color = info["color"]
            icon_kind = p_key if p_key in self.PROFILE_STYLES else "custom"

            btn_frame = QFrame()
            btn_frame.setFixedSize(120, 60)
            btn_frame.setCursor(Qt.PointingHandCursor)
            btn_frame.setStyleSheet(f"""
                QFrame {{
                    background-color: {C_BG2};
                    border: 1px solid {color};
                    border-radius: 6px;
                }}
                QFrame:hover {{
                    border: 2px solid {color};
                }}
            """)

            btn_layout = QVBoxLayout(btn_frame)
            btn_layout.setContentsMargins(6, 4, 6, 2)
            btn_layout.setSpacing(1)

            # Top row: icon + edit
            top_layout = QHBoxLayout()
            top_layout.setContentsMargins(0, 0, 0, 0)
            top_layout.setSpacing(4)

            lbl_icon = ProfileIcon(icon_kind, color, size=20)
            top_layout.addWidget(lbl_icon, alignment=Qt.AlignLeft | Qt.AlignVCenter)

            top_layout.addStretch()

            lbl_edit = QLabel("")
            lbl_edit.setStyleSheet("""
                color: #ffcc00;
                font-size: 7pt;
                font-weight: bold;
                background: transparent;
                border: none;
            """)
            lbl_edit.setAttribute(Qt.WA_TransparentForMouseEvents)
            lbl_edit.setVisible(False)
            top_layout.addWidget(lbl_edit, alignment=Qt.AlignRight | Qt.AlignVCenter)

            btn_layout.addLayout(top_layout)

            # Middle: profile name (fixed height, centered)
            lbl_name = QLabel(info["label"])
            lbl_name.setStyleSheet("""
                color: #ebdbb2;
                font-family: 'JetBrains Mono';
                font-size: 8pt;
                font-weight: bold;
                border: none;
            """)
            lbl_name.setAlignment(Qt.AlignCenter)
            lbl_name.setAttribute(Qt.WA_TransparentForMouseEvents)
            lbl_name.setFixedHeight(16)   # <--- SAME HEIGHT ON ALL BUTTONS
            btn_layout.addWidget(lbl_name, alignment=Qt.AlignCenter)

            # Alt: indicator bar
            indicator_bar = QFrame()
            indicator_bar.setFixedHeight(3)
            indicator_bar.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
            indicator_bar.setAttribute(Qt.WA_TransparentForMouseEvents)
            btn_layout.addWidget(indicator_bar, alignment=Qt.AlignBottom)

            # Left click
            btn_frame.mousePressEvent = lambda e, name=p_name: self._on_profile_left_click(name) if e.button() == Qt.LeftButton else None
            # Right click
            btn_frame.contextMenuEvent = lambda e, name=p_name: self._on_profile_right_click(name)

            self.profile_buttons[p_name] = {
                "frame": btn_frame,
                "indicator": indicator_bar,
                "color": color,
                "label": lbl_name,
                "edit_label": lbl_edit,
                "icon": lbl_icon
            }
            vp.addWidget(btn_frame)

        vp.addStretch()
        rl_main.addWidget(g_prof)

        sp.addWidget(rw)
        sp.setSizes([340, 780])
        hl.addWidget(sp)
        self.tabs.addTab(tab, "  📊 DASHBOARD / POWER  ")

    # ─── TAB 2: CURVE OPTIMIZER ──────────────────────────────────────
    def _build_tab_co(self):
        tab = QWidget()
        tab.setStyleSheet(f"background:{C_BG};")
        hl = QHBoxLayout(tab)
        hl.setSpacing(0)
        hl.setContentsMargins(0, 0, 0, 0)
        sp = QSplitter(Qt.Horizontal)
        sp.setChildrenCollapsible(False)

        # Left: All-core + Notes
        lw = QWidget()
        lw.setStyleSheet(f"background:{C_BG};")
        ll = QVBoxLayout(lw)
        ll.setSpacing(2)
        ll.setContentsMargins(8, 6, 5, 6)

        g_ac = QGroupBox(" All-Core Curve Offset ")
        vac = QVBoxLayout(g_ac)
        vac.setSpacing(4)
        vac.setContentsMargins(10, 6, 10, 6)
        row_ac = QHBoxLayout()
        row_ac.setSpacing(8)
        lbl_ac = SL("All Cores Offset:", color=C_GREEN, size=8, bold=True)
        lbl_ac.setFixedWidth(128)
        self._coall_entry = SE("", width=65, placeholder="-30")
        self._coall_entry.textChanged.connect(self._on_coall_changed)
        row_ac.addWidget(lbl_ac)
        row_ac.addWidget(self._coall_entry)
        row_ac.addStretch()
        vac.addLayout(row_ac)
        vac.addWidget(SL("Clear to re-enable per-core control.", color=C_VDGREY, size=7))
        ll.addWidget(g_ac)

        # ── CPU Live Telemetry — scroll area (replaces g_notes) ──────
        scroll_live = QScrollArea()
        scroll_live.setWidgetResizable(True)
        scroll_live.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        scroll_live.setStyleSheet(
            f"QScrollArea{{border:none;background:{C_BG};}}"
            f"QScrollBar:vertical{{background:{C_BG3};width:6px;border:none;border-radius:3px;}}"
            f"QScrollBar::handle:vertical{{background:{C_BORDER};border-radius:3px;min-height:20px;}}"
            f"QScrollBar::add-line:vertical,QScrollBar::sub-line:vertical{{height:0px;}}"
        )
        inner_live = QWidget()
        inner_live.setStyleSheet(f"background:{C_BG};")
        vinner = QVBoxLayout(inner_live)
        vinner.setSpacing(2)
        vinner.setContentsMargins(10, 4, 10, 8)

        # v_live is now directly the inner layout — no groupbox title
        v_live = vinner

        def _cosec(title: str) -> QHBoxLayout:
            """Section separator row (title + horizontal line)."""
            row = QHBoxLayout()
            row.setSpacing(4)
            row.setContentsMargins(0, 3, 0, 1)
            row.addWidget(SL(title, color=C_ORANGE, size=7, bold=True))
            ln = QFrame()
            ln.setFrameShape(QFrame.HLine)
            ln.setStyleSheet(f"color:{C_ORANGE};max-height:1px;")
            row.addWidget(ln, stretch=1)
            return row

        # ── Temperatures ──────────────────────────────────────────────
        v_live.addLayout(_cosec("TEMPERATURES"))
        self._co_temp_rows: dict = {}
        for tag in ["Tctl", "Tccd1", "Tccd2"]:
            trow = QHBoxLayout()
            trow.setSpacing(4)
            trow.setContentsMargins(0, 1, 0, 1)
            n_lbl = SL(f"{tag}:", color=C_GREEN, size=8)
            n_lbl.setFixedWidth(46)
            v_lbl = SL("—", color=C_CYAN, size=8, bold=True)
            v_lbl.setFixedWidth(72)
            trow.addWidget(n_lbl)
            trow.addWidget(v_lbl)
            trow.addStretch()
            v_live.addLayout(trow)
            self._co_temp_rows[tag] = v_lbl

        # ── Socket Power ──────────────────────────────────────────────
        v_live.addLayout(_cosec("SOCKET POWER"))
        prow = QHBoxLayout()
        prow.setSpacing(10)
        prow.setContentsMargins(0, 1, 0, 1)
        self._co_pwr_cur = SL("—", color=C_CYAN, size=8, bold=True)
        self._co_pwr_avg = SL("avg: —", color=C_DGREY, size=8)
        prow.addWidget(self._co_pwr_cur)
        prow.addWidget(self._co_pwr_avg)
        prow.addStretch()
        v_live.addLayout(prow)

        # ── Per-Core Grid ─────────────────────────────────────────────
        v_live.addLayout(_cosec("PER-CORE"))
        # Header — stretch ratios: Core=1, T0=2, T1=2, Pwr=2, Bar=3
        hdr = QHBoxLayout()
        hdr.setSpacing(4)
        hdr.setContentsMargins(0, 0, 0, 1)
        for txt, stretch in [("Core", 1), ("T0", 2), ("T1", 2), ("Pwr", 2), ("", 8)]:
            lb = SL(txt, color=C_GREEN, size=7, bold=True)
            lb.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
            hdr.addWidget(lb, stretch=stretch)
        v_live.addLayout(hdr)
        v_live.addWidget(hsep())

        self._co_live_rows: list = []   # (lbl_id, lbl_t0, lbl_t1, lbl_pwr, lbl_bar)
        for i in range(12):
            crow = QHBoxLayout()
            crow.setSpacing(4)
            crow.setContentsMargins(0, 1, 0, 1)
            lbl_id  = SL(f"#{i:02d}", color=C_BLUE,   size=8)
            lbl_t0  = SL("—",         color=C_CYAN,   size=8)
            lbl_t1  = SL("—",         color=C_DGREY,  size=8)
            lbl_pwr = SL("—",         color=C_YELLOW, size=8)
            lbl_bar = SL("",          color=C_ORANGE, size=8)
            for lb in (lbl_id, lbl_t0, lbl_t1, lbl_pwr, lbl_bar):
                lb.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
            crow.addWidget(lbl_id,  stretch=1)
            crow.addWidget(lbl_t0,  stretch=2)
            crow.addWidget(lbl_t1,  stretch=2)
            crow.addWidget(lbl_pwr, stretch=2)
            crow.addWidget(lbl_bar, stretch=8)
            v_live.addLayout(crow)
            self._co_live_rows.append((lbl_id, lbl_t0, lbl_t1, lbl_pwr, lbl_bar))

        # ── Boost / Governor / EPP ────────────────────────────────────
        v_live.addLayout(_cosec("BOOST / GOVERNOR"))
        self._co_boost_lbl = SL("—", color=C_GREEN, size=8)
        self._co_boost_lbl.setTextFormat(Qt.RichText)
        v_live.addWidget(self._co_boost_lbl)

        scroll_live.setWidget(inner_live)
        ll.addWidget(scroll_live, 1)
        sp.addWidget(lw)

        # Right: Per-core CO + CPPC
        rw = QWidget()
        rw.setStyleSheet(f"background:{C_BG};")
        rl = QHBoxLayout(rw)
        rl.setSpacing(5)
        rl.setContentsMargins(0, 0, 5, 6)

        mw = QWidget()
        ml = QVBoxLayout(mw)
        ml.setSpacing(0)
        ml.setContentsMargins(0, 0, 0, 0)

        g_pc = QGroupBox(" Per-Core Curve Optimizer Offsets ")
        g_pc.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        vpc = QVBoxLayout(g_pc)
        vpc.setSpacing(0)
        vpc.setContentsMargins(0, 0, 0, 0)

        self._co_container = QWidget()
        self._co_layout = QGridLayout(self._co_container)
        self._co_layout.setContentsMargins(12, 10, 10, 10)
        self._co_layout.setSpacing(6)

        vpc.addWidget(self._co_container)
        ml.addWidget(g_pc, stretch=1)
        rl.addWidget(mw, stretch=1)

        rr = QWidget()
        rrl = QVBoxLayout(rr)
        rrl.setSpacing(0)
        rrl.setContentsMargins(0, 0, 0, 0)

        g_cppc = QGroupBox(" CPPC Performance Capabilities ")
        g_cppc.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        vcppc = QVBoxLayout(g_cppc)
        vcppc.setSpacing(3)
        vcppc.setContentsMargins(6, 4, 6, 6)

        hdr2 = QHBoxLayout()
        hdr2.setSpacing(0)
        for t in ["CPU", "Lowest", "Effic.", "Guar.", "Highest"]:
            lb = SL(t, bold=True, color=C_DGREY, size=8)
            lb.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
            hdr2.addWidget(lb)
        vcppc.addLayout(hdr2)
        vcppc.addWidget(hsep())

        self._cppc_rows = []
        for i in range(24):
            row = QHBoxLayout()
            row.setSpacing(0)
            row.setContentsMargins(0, 1, 0, 1)
            lbl_id = SL(f"#{i:02d}", color=C_BLUE, size=8)
            lbl_id.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
            lbl_lo = SL("—", color=C_VDGREY, size=8)
            lbl_lo.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
            lbl_eff = SL("—", color=C_VDGREY, size=8)
            lbl_eff.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
            lbl_gua = SL("—", color=C_VDGREY, size=8)
            lbl_gua.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
            lbl_high = SL("—", color=C_CYAN, bold=True, size=8)
            lbl_high.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
            for lb in [lbl_id, lbl_lo, lbl_eff, lbl_gua, lbl_high]:
                row.addWidget(lb, alignment=Qt.AlignVCenter)
            vcppc.addLayout(row)
            self._cppc_rows.append((lbl_lo, lbl_eff, lbl_gua, lbl_high))

        btn_row = QHBoxLayout()
        btn_cppc = QPushButton("↻ Fetch CPPC")
        btn_cppc.setObjectName("run_button")
        btn_cppc.setFixedHeight(22)
        btn_cppc.clicked.connect(self._fetch_cppc)
        btn_row.addStretch()
        btn_row.addWidget(btn_cppc)
        vcppc.addLayout(btn_row)

        rrl.addWidget(g_cppc, stretch=1)
        rl.addWidget(rr, stretch=1)

        sp.addWidget(rw)
        sp.setSizes([305, 810])
        hl.addWidget(sp)
        self.tabs.addTab(tab, "  ⚙️ CURVE OPTIMIZER  ")
        self._populate_co([])

    def _populate_co(self, core_data):
        self._co_core_wgts = []
        self._clear_layout(self._co_layout)

        for col, t in enumerate(["ID", "CCD", "CCX", "Core", "Offset", "Status"]):
            lbl = SL(t, bold=True, color=C_DGREY, size=8)
            if t == "Offset":
                self._co_layout.addWidget(lbl, 0, col, alignment=Qt.AlignHCenter)
            elif t == "Status":
                self._co_layout.addWidget(lbl, 0, col, alignment=Qt.AlignRight)
            else:
                self._co_layout.addWidget(lbl, 0, col, alignment=Qt.AlignLeft)

        self._co_layout.addWidget(hsep(), 1, 0, 1, 6)

        DISABLED = {2, 3, 10, 12}
        row_idx = 2
        for c in core_data:
            cid = c.get("id", 0)
            ccd = c.get("ccd", 0)
            ccx = c.get("ccx", 0)
            core = c.get("core", 0)
            off = c.get("coper", 0)
            dis = cid in DISABLED

            col_id = C_VDGREY if dis else C_BLUE

            lbl_id = SL(f"#{cid}", bold=True, color=col_id, size=8)
            lbl_ccd = SL(str(ccd), color=C_GREY, size=8)
            lbl_ccx = SL(str(ccx), color=C_GREY, size=8)
            lbl_core = SL(str(core), color=C_GREY, size=8)

            entry = SE(str(off), width=35)
            entry.setAlignment(Qt.AlignCenter)

            if dis:
                entry.setEnabled(False)
                entry.set_grey(True)
                stxt, scol = "DISABLED", C_VDGREY
            else:
                entry.textChanged.connect(self._mark)
                stxt, scol = "ACTIVE", C_GREEN

            lbl_st = SL(stxt, color=scol, size=7)

            self._co_layout.addWidget(lbl_id, row_idx, 0, alignment=Qt.AlignLeft)
            self._co_layout.addWidget(lbl_ccd, row_idx, 1, alignment=Qt.AlignLeft)
            self._co_layout.addWidget(lbl_ccx, row_idx, 2, alignment=Qt.AlignLeft)
            self._co_layout.addWidget(lbl_core, row_idx, 3, alignment=Qt.AlignLeft)
            self._co_layout.addWidget(entry, row_idx, 4, alignment=Qt.AlignHCenter)
            self._co_layout.addWidget(lbl_st, row_idx, 5, alignment=Qt.AlignRight)

            if not dis:
                self._co_core_wgts.append((cid, entry, lbl_st))
            row_idx += 1

        self._co_layout.setRowStretch(row_idx, 1)
        self._sync_coall()

    def _on_coall_changed(self, text):
        self._sync_coall()
        self._mark()

    def _sync_coall(self):
        has = bool(self._coall_entry and self._coall_entry.text().strip())
        for cid, entry, lbl in self._co_core_wgts:
            # Add edit mode check
            if self.edit_mode and self.current == self.edit_profile:
                entry.set_grey(has)
                entry.setEnabled(not has and self.edit_mode)
            else:
                entry.set_grey(True)
                entry.setEnabled(False)
            if has:
                lbl.setText("OVERRIDDEN")
                lbl.setStyleSheet(f"color:{C_YELLOW};")
            else:
                lbl.setText("ACTIVE")
                lbl.setStyleSheet(f"color:{C_GREEN};")

    # ─── CO LIVE TELEMETRY ────────────────────────────────────────────

    def _find_hwmon_by_name(self, name: str) -> str | None:
        """Returns the /sys/class/hwmon/* path for a given driver name."""
        try:
            for entry in os.listdir('/sys/class/hwmon'):
                path = f'/sys/class/hwmon/{entry}'
                nf = f'{path}/name'
                if os.path.exists(nf):
                    with open(nf) as f:
                        if f.read().strip() == name:
                            return path
        except Exception:
            pass
        return None

    def _init_co_live_handles(self):
        """
        Discover hwmon paths at startup and open the persistent file handles.
        k10temp  → Tctl / Tccd1 / Tccd2
        zenergy  → Esocket0 + Ecore000-Ecore011 (per-physical-core)
        cpufreq  → scaling_cur_freq × 24 thread
        topology → physical core groups via core_id
        dell_smm → DO NOT TOUCH
        """
        self._co_k10temp_paths:       list = []   # [(tag, path)]
        self._co_zenergy_socket_path: str | None = None
        self._co_zenergy_core_paths:  list = []   # [(ecore_idx, path)]
        self._co_freq_paths:          list = []   # [(cpu_idx, path)]
        self._co_core_topology:       list = []   # [(core_id, [cpu_idx, ...])]

        # ── k10temp ─────────────────────────────────────────────────
        k10_dir = self._find_hwmon_by_name('k10temp')
        if k10_dir:
            wanted = {'Tctl', 'Tccd1', 'Tccd2'}
            for i in range(1, 12):
                lp = f'{k10_dir}/temp{i}_label'
                vp = f'{k10_dir}/temp{i}_input'
                if not (os.path.exists(lp) and os.path.exists(vp)):
                    continue
                try:
                    with open(lp) as f:
                        lbl = f.read().strip()
                    if lbl in wanted:
                        self._co_k10temp_paths.append((lbl, vp))
                except Exception:
                    pass

        # ── zenergy ─────────────────────────────────────────────────
        zen_dir = self._find_hwmon_by_name('zenergy')
        if zen_dir:
            # glob instead of a number-based range — finds all existing energy*_label files
            label_files = sorted(
                glob.glob(f'{zen_dir}/energy*_label'),
                key=lambda p: int(p.split('/energy')[1].split('_')[0])
            )
            for lp in label_files:
                ep = lp.replace('_label', '_input')
                if not os.path.exists(ep):
                    continue
                try:
                    with open(lp) as f:
                        lbl = f.read().strip()
                    if lbl == 'Esocket0':
                        self._co_zenergy_socket_path = ep
                    elif lbl.startswith('Ecore') and lbl[5:].isdigit():
                        self._co_zenergy_core_paths.append((int(lbl[5:]), ep))
                except Exception:
                    pass
            self._co_zenergy_core_paths.sort(key=lambda x: x[0])

        # ── CPU topology and frequency paths ──────────────────────────
        # Confirmed via corefreq -m: contiguous SMT layout.
        # cpu[i*2]  = physical core i, thread 0
        # cpu[i*2+1]= physical core i, thread 1
        # Physical core count = number of zenergy Ecore entries.
        # Don't rely on thread_siblings_list: on AMD it can reset per CCD.
        freq_base = '/sys/devices/system/cpu'

        # Collect frequency paths for all logical CPUs (numeric order)
        all_freq = sorted(
            glob.glob(f'{freq_base}/cpu[0-9]*/cpufreq/scaling_cur_freq'),
            key=lambda p: int(p.split('/')[-3][3:])
        )
        for fpath in all_freq:
            cpu_name = fpath.split('/')[-3]
            if cpu_name[3:].isdigit():
                self._co_freq_paths.append((int(cpu_name[3:]), fpath))

        # Ecore000+001 = core 0, Ecore002+003 = core 1 ... (even index)
        # 24 Ecore entries → 12 physical cores
        n_phys = len(self._co_zenergy_core_paths) // 2

        # Topology: phys_core_i → (cpu[i*2], cpu[i*2+1])  — contiguous SMT layout
        self._co_core_topology = [
            (i, [i * 2, i * 2 + 1])
            for i in range(n_phys)
        ]

        # ── Gov / EPP extra paths ─────────────────────────────────
        extra_paths = [
            '/sys/devices/system/cpu/cpu0/cpufreq/scaling_governor',
            '/sys/devices/system/cpu/cpu0/cpufreq/energy_performance_preference',
            '/sys/devices/system/cpu/cpufreq/boost',   # D4
        ]

        # ── Open all files ─────────────────────────────────────────
        all_paths = (
            [p for _, p in self._co_k10temp_paths]
            + ([self._co_zenergy_socket_path] if self._co_zenergy_socket_path else [])
            + [p for _, p in self._co_zenergy_core_paths]
            + [p for _, p in self._co_freq_paths]
            + extra_paths
        )
        for path in all_paths:
            if path and path not in self._co_live_handles:
                try:
                    self._co_live_handles[path] = open(path, 'r')
                except Exception:
                    self._co_live_handles[path] = None

    def _co_live_read(self, path: str) -> str | None:
        """Reads via seek(0) through the persistent handle; returns None on error."""
        fh = self._co_live_handles.get(path)
        try:
            if fh is None:
                fh = open(path, 'r')
                self._co_live_handles[path] = fh
            fh.seek(0)
            return fh.read().strip()
        except Exception:
            if fh:
                try:
                    fh.close()
                except Exception:
                    pass
            self._co_live_handles[path] = None
            return None

    def resizeEvent(self, event):
        # D5: drop the cached "█" advance so it is re-metricked once after a resize
        self._co_bar_char_w = 0
        super().resizeEvent(event)

    def _co_set_text(self, lbl, text):
        """D3: setText only when the text actually changed for this label."""
        wid = id(lbl)
        if self._co_live_text_cache.get(wid) != text:
            self._co_live_text_cache[wid] = text
            lbl.setText(text)

    def _co_set_color(self, lbl, color):
        """D3: setStyleSheet('color:...') only when the color changed."""
        wid = id(lbl)
        if self._co_live_style_cache.get(wid) != color:
            self._co_live_style_cache[wid] = color
            lbl.setStyleSheet(f"color:{color};")

    def _update_co_live(self):
        """1-second CO Live Telemetry update — pure sysfs seek(), no subprocess."""
        if not self.isVisible():
            return

        now = time.monotonic()

        # ── Temperatures (k10temp) ────────────────────────────────────
        for tag, path in self._co_k10temp_paths:
            v_lbl = self._co_temp_rows.get(tag)
            if v_lbl is None:
                continue
            raw = self._co_live_read(path)
            if raw is None:
                self._co_set_text(v_lbl, "—")
                continue
            try:
                temp = int(raw) / 1000.0
            except ValueError:
                self._co_set_text(v_lbl, "—")
                continue
            color = C_CYAN if temp < 70 else (C_YELLOW if temp < 85 else C_STOP)
            self._co_set_text(v_lbl, f"{temp:.1f} °C")
            self._co_set_color(v_lbl, color)

        # ── Socket Power (zenergy Esocket0) ──────────────────────────
        socket_watt = 0.0
        if self._co_zenergy_socket_path:
            raw = self._co_live_read(self._co_zenergy_socket_path)
            if raw is not None:
                try:
                    joules = int(raw) / 1_000_000
                    prev = self._co_live_socket_prev
                    if prev is not None:
                        dt = now - prev[1]
                        dj = joules - prev[0]
                        if dt > 0 and dj >= 0:
                            socket_watt = dj / dt
                    self._co_live_socket_prev = (joules, now)
                except ValueError:
                    pass

        self._co_live_socket_buf.append(socket_watt)
        avg_w = (sum(self._co_live_socket_buf) / len(self._co_live_socket_buf)
                 if self._co_live_socket_buf else 0.0)
        if socket_watt > 0 or avg_w > 0:
            self._co_set_text(self._co_pwr_cur, f"{socket_watt:.1f} W")
            self._co_set_text(self._co_pwr_avg, f"avg: {avg_w:.1f} W")

        # ── Per-Core Power (zenergy Ecore*) ──────────────────────────
        # dict keyed by Ecore index order; matched up against the topology order
        core_watts: dict = {}
        for ecore_idx, epath in self._co_zenergy_core_paths:
            raw = self._co_live_read(epath)
            if raw is None:
                continue
            try:
                joules = int(raw) / 1_000_000
                prev = self._co_live_core_prev.get(epath)
                if prev is not None:
                    dt = now - prev[1]
                    dj = joules - prev[0]
                    if dt > 0 and dj >= 0:
                        core_watts[ecore_idx] = dj / dt
                self._co_live_core_prev[epath] = (joules, now)
            except ValueError:
                pass

        # ── Per-Thread Frekanslar ─────────────────────────────────────
        freq_map: dict = {}
        for cpu_idx, fpath in self._co_freq_paths:
            raw = self._co_live_read(fpath)
            if raw:
                try:
                    freq_map[cpu_idx] = int(raw) // 1000
                except ValueError:
                    pass

        # ── Row update ──────────────────────────────────────────
        for i, (first_cpu, threads) in enumerate(self._co_core_topology):
            if i >= len(self._co_live_rows):
                break
            lbl_id, lbl_t0, lbl_t1, lbl_pwr, lbl_bar = self._co_live_rows[i]

            t0 = freq_map.get(threads[0], 0) if threads else 0
            t1 = freq_map.get(threads[1], 0) if len(threads) > 1 else 0
            t0_col = C_CYAN   if t0 > 400 else C_VDGREY
            t1_col = C_DGREY  if t1 > 400 else C_VDGREY
            self._co_set_text(lbl_t0, f"{t0:4d}")
            self._co_set_color(lbl_t0, t0_col)
            self._co_set_text(lbl_t1, f"{t1:4d}")
            self._co_set_color(lbl_t1, t1_col)

            # Ecore[i*2] and Ecore[i*2+1] are the two threads of the same physical core
            # → same MSR value; use the even index
            pwr = core_watts.get(i * 2)
            if pwr is not None:
                pcol = C_BLUE if pwr < 3 else (C_YELLOW if pwr < 8 else C_ORANGE)
                self._co_set_text(lbl_pwr, f"{pwr:4.1f}W")
                self._co_set_color(lbl_pwr, pcol)
                # D5: "█" advance width is font-invariant; compute once, cache
                char_w = self._co_bar_char_w
                if char_w <= 0:
                    char_w = lbl_bar.fontMetrics().horizontalAdvance("█")
                    self._co_bar_char_w = char_w
                n_chars = max(8, lbl_bar.width() // char_w) if char_w > 0 else 8
                filled = min(n_chars, int(pwr / 15.0 * n_chars))
                self._co_set_text(lbl_bar, "█" * filled + "░" * (n_chars - filled))
                self._co_set_color(lbl_bar, pcol)
            else:
                self._co_set_text(lbl_pwr, "  —  ")
                self._co_set_color(lbl_pwr, C_VDGREY)
                self._co_set_text(lbl_bar, "")

        # ── Boost / Gov / EPP ─────────────────────────────────────────
        boost_raw = self._co_live_read('/sys/devices/system/cpu/cpufreq/boost')
        boost_on = (boost_raw == "1") if boost_raw is not None else None

        gov_raw = self._co_live_read(
            '/sys/devices/system/cpu/cpu0/cpufreq/scaling_governor')
        epp_raw = self._co_live_read(
            '/sys/devices/system/cpu/cpu0/cpufreq/energy_performance_preference')

        if boost_on is None:
            b_str = f'<span style="color:{C_VDGREY}">Boost: ?</span>'
        elif boost_on:
            b_str = f'<span style="color:{C_GREEN}">Boost: ON</span>'
        else:
            b_str = f'<span style="color:{C_STOP}">Boost: OFF</span>'

        sep  = f'<span style="color:{C_VDGREY}"> · </span>'
        gov  = gov_raw or "?"
        epp  = epp_raw or "?"
        self._co_set_text(
            self._co_boost_lbl,
            f'{b_str}{sep}'
            f'<span style="color:{C_GREY}">Gov: {gov}</span>{sep}'
            f'<span style="color:{C_DGREY}">EPP: {epp}</span>'
        )

    # A1+A2: A single persistent QProcess — no new object is created each call,
    # no leaks; the flag is reset on both finished and errorOccurred.
    _CPPC_RE = None  # B8: module-global regex (lazy init)

    def _fetch_cppc(self):
        if self._cppc_fetching:
            return
        self._cppc_fetching = True

        # Create the persistent proc once if it doesn't exist yet
        if not hasattr(self, '_cppc_proc') or self._cppc_proc is None:
            corefreq_cli_path = find_tool("corefreq-cli")
            if not corefreq_cli_path:
                self._cppc_fetching = False
                return
            self._cppc_proc = QProcess(self)
            self._cppc_proc.setProgram(corefreq_cli_path)
            self._cppc_proc.setArguments(["-z"])
            # Read all output in bulk on finished (no partial-parse errors)
            self._cppc_proc.finished.connect(self._on_cppc_finished)
            self._cppc_proc.errorOccurred.connect(self._on_cppc_error)

        if self._cppc_proc.state() != QProcess.NotRunning:
            # Previous one is still running, skip
            self._cppc_fetching = False
            return
        try:
            self._cppc_proc.start()
        except Exception as e:
            self._log(f"CPPC fetch error: {e}")
            self._cppc_fetching = False

    def _on_cppc_finished(self, exit_code, exit_status):
        """All output is ready — parse it in one go. A2: the flag is reset here."""
        try:
            txt = self._cppc_proc.readAllStandardOutput().data().decode(errors="replace")
            self._parse_cppc_output(txt)
        finally:
            self._cppc_fetching = False  # A2: reset in every case

    def _on_cppc_error(self, error):
        """Reset the flag on error too. A2."""
        self._cppc_fetching = False

    def _parse_cppc_output(self, txt):
        # B8: the regex is compiled at module/class level (not recompiled every 3s)
        if RyzenAdjGUI._CPPC_RE is None:
            import re
            RyzenAdjGUI._CPPC_RE = re.compile(
                r'CPU\s+#(\d+)\s+([\d.]+)\s*\(\s*\d+\)\s+([\d.]+)\s*\(\s*\d+\)\s+([\d.]+)\s*\(\s*\d+\)\s+([\d.]+)'
            )
        pat = RyzenAdjGUI._CPPC_RE
        parsed = {}
        for line in txt.splitlines():
            m = pat.search(line)
            if m:
                parsed[int(m.group(1))] = (m.group(2), m.group(3), m.group(4), m.group(5))
        for i, (lbl_lo, lbl_eff, lbl_gua, lbl_high) in enumerate(self._cppc_rows):
            if i in parsed:
                lo, eff, gua, high = parsed[i]
                lbl_lo.setText(lo)
                lbl_lo.setStyleSheet(f"color:{C_DGREY};")
                lbl_eff.setText(eff)
                lbl_eff.setStyleSheet(f"color:{C_GREY};")
                lbl_gua.setText(gua)
                lbl_gua.setStyleSheet(f"color:{C_GREEN};")
                try:
                    v = float(high)
                    hc = C_CYAN if v >= 4200 else C_GREEN if v >= 4000 else C_YELLOW if v >= 3800 else C_ORANGE
                except Exception:
                    hc = C_GREY
                lbl_high.setText(high)
                lbl_high.setStyleSheet(f"color:{hc};font-weight:bold;")

    # ─── TAB 3: COREFREQ TERMINAL ────────────────────────────────────
    def _build_tab_corefreq_terminal(self):
        tab = QWidget()
        tab.setStyleSheet(f"background:{C_BG};")
        root = QVBoxLayout(tab)
        root.setSpacing(4)
        root.setContentsMargins(6, 6, 6, 4)

        top = QHBoxLayout()
        top.setSpacing(6)
        self.btn_cf_run = QPushButton("▶ Run")
        self.btn_cf_run.setObjectName("run_button")
        self.btn_cf_run.setFixedHeight(22)
        self.btn_cf_run.clicked.connect(self._run_cf_terminal)
        top.addWidget(self.btn_cf_run)

        top.addWidget(SL("Command:", color=C_GREY, size=8))
        self.cf_cmd_edit = QLineEdit("corefreq-cli -s")
        self.cf_cmd_edit.setFixedHeight(22)
        self.cf_cmd_edit.setFont(get_font(8))
        self.cf_cmd_edit.setStyleSheet(f"background:{C_BG2};color:{C_YELLOW};border:1px solid {C_BORDER};border-radius:2px;padding:1px 3px;")
        top.addWidget(self.cf_cmd_edit, stretch=1)

        top.addWidget(SL("Refresh (s):", color=C_GREY, size=8))
        self.cf_refresh_spin = QSpinBox()
        self.cf_refresh_spin.setRange(0, 60)
        self.cf_refresh_spin.setValue(0)
        self.cf_refresh_spin.setFixedWidth(55)
        self.cf_refresh_spin.setFixedHeight(22)
        self.cf_refresh_spin.setSpecialValueText("OFF")
        self.cf_refresh_spin.valueChanged.connect(self._on_cf_refresh_changed)
        top.addWidget(self.cf_refresh_spin)
        top.addWidget(SL("(0 = static)", color=C_VDGREY, size=7))
        top.addStretch()
        root.addLayout(top)

        self.cf_output = QTextEdit()
        self.cf_output.setReadOnly(True)
        self.cf_output.setFont(get_font(9))
        self.cf_output.setStyleSheet(f"background:{C_BG3};color:#a89984;border:none;")
        self.cf_output.setPlaceholderText('Enter a corefreq-cli command above and press "Run".')
        root.addWidget(self.cf_output, stretch=1)

        quick = QHBoxLayout()
        quick.setSpacing(4)
        for label, cmd in [
            ("Sys Info", "corefreq-cli -s"),
            ("Kernel", "corefreq-cli -k"),
            ("Topology", "corefreq-cli -m"),
            ("Perf Cap", "corefreq-cli -z"),
            ("Memory", "corefreq-cli -M"),
        ]:
            btn = QPushButton(label)
            btn.setObjectName("run_button")
            btn.setFixedHeight(20)
            btn.clicked.connect(lambda checked, c=cmd: self._set_cf_command(c))
            quick.addWidget(btn)

        btn_term = QPushButton("🖥️ Run in Terminal")
        btn_term.setObjectName("run_button")
        btn_term.setFixedHeight(20)
        btn_term.clicked.connect(self._run_cf_in_terminal)
        quick.addWidget(btn_term)

        quick.addStretch()
        root.addLayout(quick)

        self.tabs.addTab(tab, "  🖥️ COREFREQ TERMINAL  ")

    def _set_cf_command(self, cmd):
        self.cf_cmd_edit.setText(cmd)
        self._run_cf_terminal()

    def _run_cf_terminal(self):
        cmd = self.cf_cmd_edit.text().strip()
        if not cmd:
            self.cf_output.append("No command entered.")
            return
        self.cf_output.append(f"$ {cmd}")
        if self._cf_process and self._cf_process.state() == QProcess.Running:
            self._cf_process.kill()
            self._cf_process.waitForFinished(500)
        self._cf_process = QProcess(self)
        self._cf_process.readyReadStandardOutput.connect(self._cf_read_output)
        self._cf_process.readyReadStandardError.connect(self._cf_read_error)
        self._cf_process.finished.connect(self._cf_finished)
        try:
            parts = shlex.split(cmd)
        except ValueError as e:
            self.cf_output.append(f"ERROR: invalid command syntax: {e}")
            return
        prog = parts[0] if parts else "corefreq-cli"
        args = parts[1:] if len(parts) > 1 else []
        self._cf_process.start(prog, args)

    def _cf_read_output(self):
        data = self._cf_process.readAllStandardOutput().data().decode()
        if data:
            self.cf_output.append(data.rstrip())

    def _cf_read_error(self):
        data = self._cf_process.readAllStandardError().data().decode()
        if data:
            self.cf_output.append(f"ERROR: {data.rstrip()}")

    def _cf_finished(self, code, status):
        if code != 0:
            self.cf_output.append(f"Process finished with code {code} (status {status})")
        else:
            self.cf_output.append("Process finished successfully.")
        cur = self.cf_output.textCursor()
        cur.movePosition(QTextCursor.MoveOperation.End)
        self.cf_output.setTextCursor(cur)

    def _run_cf_in_terminal(self):
        corefreq_cli_path = find_tool("corefreq-cli")
        cmd = corefreq_cli_path or "corefreq-cli"
        terminals = [
            ("xterm", ["-e"]),
            ("konsole", ["-e"]),
            ("gnome-terminal", ["--"]),
            ("alacritty", ["-e"]),
            ("kitty", ["-e"]),
        ]
        for term, args in terminals:
            if shutil.which(term):
                full_cmd = [term] + args + cmd.split()
                try:
                    subprocess.Popen(full_cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                    self.cf_output.append(f"Opened {term} with: {cmd}")
                    return
                except Exception as e:
                    self.cf_output.append(f"Failed to launch {term}: {e}")
                    continue
        self.cf_output.append("No terminal emulator found. Please run manually.")

    def _on_cf_refresh_changed(self, val):
        if self._cf_timer:
            self._cf_timer.stop()
            self._cf_timer = None
        if val > 0:
            self._cf_timer = QTimer(self)
            self._cf_timer.setInterval(val * 1000)
            self._cf_timer.timeout.connect(self._run_cf_terminal)
            self._cf_timer.start()

    # ─── TAB 4: GPU TUNING ──────────────────────────────────────────────
    def _build_tab_gpu(self):
        tab = QWidget()
        tab.setStyleSheet(f"background:{C_BG};")
        layout = QVBoxLayout(tab)

        # GPU Status
        info_group = QGroupBox(" GPU Status ")
        info_layout = QHBoxLayout(info_group)
        self.gpu_temp = SL("Temp: -- °C", color=C_GREY)
        self.gpu_mem_temp = SL("Mem Temp: -- °C", color=C_GREY)
        self.gpu_hotspot_temp = SL("Hotspot: -- °C", color=C_GREY)
        self.gpu_power = SL("Power: -- W", color=C_GREY)
        self.gpu_clock = SL("Clock: -- MHz", color=C_GREY)
        self.gpu_mem_clock = SL("Mem Clock: -- MHz", color=C_GREY)
        info_layout.addWidget(self.gpu_temp)
        info_layout.addWidget(self.gpu_mem_temp)
        info_layout.addWidget(self.gpu_hotspot_temp)
        info_layout.addWidget(self.gpu_power)
        info_layout.addWidget(self.gpu_clock)
        info_layout.addWidget(self.gpu_mem_clock)
        info_layout.addStretch()

        # Save As + profile combo live here now, far right of the status row.
        self.btn_save_profile = QPushButton("💾 Save As")
        self.btn_save_profile.setObjectName("save_button")
        self.btn_save_profile.setFixedHeight(22)
        self.btn_save_profile.setFixedWidth(80)
        self.btn_save_profile.clicked.connect(self._save_profile_as)
        info_layout.addWidget(self.btn_save_profile)

        # The current default (auto-load) profile is marked with a star
        # directly in the combo box item text instead of a separate label
        # (see _refresh_profile_list / _refresh_default_star).
        self.profile_combo = QComboBox()
        self.profile_combo.setFixedHeight(22)
        self.profile_combo.setMinimumWidth(150)
        self.profile_combo.activated.connect(self._on_profile_selected)
        info_layout.addWidget(self.profile_combo)
        layout.addWidget(info_group)

        # Default/Delete toolbar: nvcurve's own `profile default` /
        # `autoload` mechanism (see root_helper.py op_set_default_gpu_profile
        # / op_run_gpu_autoload). No extra daemon/service — applied once at
        # tray startup. "Default" is a toggle: click once to mark the
        # selected profile as default (★ appears in the combo), click again
        # on the same profile to unmark it.
        profile_toolbar = QHBoxLayout()
        profile_toolbar.addStretch()

        self.btn_set_default_profile = QPushButton("⭐ Default")
        self.btn_set_default_profile.setFixedHeight(22)
        self.btn_set_default_profile.setFixedWidth(90)
        self.btn_set_default_profile.clicked.connect(self._toggle_default_gpu_profile)
        profile_toolbar.addWidget(self.btn_set_default_profile)

        self.btn_delete_profile = QPushButton("🗑 Delete")
        self.btn_delete_profile.setFixedHeight(22)
        self.btn_delete_profile.setFixedWidth(90)
        self.btn_delete_profile.clicked.connect(self._delete_gpu_profile)
        profile_toolbar.addWidget(self.btn_delete_profile)
        layout.addLayout(profile_toolbar)

        # Point Info & TGP Controls
        info_panel = QGroupBox(" Point Info & Controls ")
        info_panel_layout = QHBoxLayout(info_panel)

        self.point_index_label = SL("Selected: -", color=C_GREY)
        self.point_voltage_label = SL("Voltage: - mV", color=C_GREY)
        self.point_freq_label = SL("Freq: - MHz", color=C_GREY)
        self.point_offset_label = SL("Offset: - MHz", color=C_GREY)

        self.point_offset_spin = QSpinBox()
        self.point_offset_spin.setRange(-1000, 1000)
        self.point_offset_spin.setValue(0)
        self.point_offset_spin.setSuffix(" MHz")
        self.point_offset_spin.setSingleStep(1)
        self.point_offset_spin.setFixedWidth(100)
        self.point_offset_spin.valueChanged.connect(self._on_point_offset_spin_changed)
        self.point_offset_spin.setEnabled(False)

        info_panel_layout.addWidget(self.point_index_label)
        info_panel_layout.addWidget(self.point_voltage_label)
        info_panel_layout.addWidget(self.point_freq_label)
        info_panel_layout.addWidget(self.point_offset_label)
        info_panel_layout.addWidget(self.point_offset_spin)

        # Sola yaslamak ve arayı açmak için boşluk ekliyoruz
        info_panel_layout.addStretch()

        # ==========================================
        # 1. BÖLÜM: Flatten After Index
        # ==========================================
        limit_label = SL("Flatten After Index:", color=C_GREY)
        self.limit_spin = QSpinBox()
        self.limit_spin.setRange(-1, 126)
        self.limit_spin.setSpecialValueText(" ")
        self.limit_spin.setValue(-1)
        self.limit_spin.setSingleStep(1)
        self.limit_spin.setFixedHeight(25)
        self.limit_spin.setFixedWidth(70)
        self.limit_spin.editingFinished.connect(self._on_flatten_entered)

        info_panel_layout.addWidget(limit_label)
        info_panel_layout.addWidget(self.limit_spin)

        info_panel_layout.addSpacing(15) # Araya biraz boşluk

        # ==========================================
        # ARA BÖLÜCÜ ÇİZGİ
        # ==========================================
        separator = QFrame()
        separator.setFrameShape(QFrame.VLine)
        separator.setFrameShadow(QFrame.Sunken)
        separator.setStyleSheet(f"color: {C_GREY};")
        info_panel_layout.addWidget(separator)

        info_panel_layout.addSpacing(15) # Çizgiden sonra boşluk

        # ==========================================
        # 2. BÖLÜM: GPU TGP (130W - 175W)
        # ==========================================

        g_ctdp = QGroupBox(" cTDP ")

        # Başlığı sol üste (top left) taşır ve boşlukları ayarlar
        g_ctdp.setStyleSheet("""
            QGroupBox {
                margin-top: 1.5ex; /* Kutu içeriğinin başlıkla çakışmaması için üst boşluk */
            }
            QGroupBox::title {
                subcontrol-origin: margin;
                subcontrol-position: top left; /* Başlığı tam sol üste sabitler */
                padding: 0 5px; /* Yazının sağından solundan 5px boşluk bırakır (çizgiyi keser) */
                left: 10px; /* Soldan ne kadar içeride başlayacağını piksel olarak belirler */
            }
        """)
        self.tgp_slider = QSlider(Qt.Horizontal)
        self.tgp_slider.setRange(130, 175)
        self.tgp_slider.setValue(130)
        self.tgp_slider.setFixedWidth(100) # Tasarımı bozmaması için genişliği sabitledik

        self.tgp_value_label = SL("130W", color=C_CYAN)
        self.tgp_value_label.setFixedWidth(40)

        self.tgp_slider.valueChanged.connect(
            lambda v: self.tgp_value_label.setText(f"{v}W")
        )

        self.btn_apply_tgp = QPushButton("🚀 Apply")
        self.btn_apply_tgp.setObjectName("apply_button")
        self.btn_apply_tgp.setFixedHeight(22)
        self.btn_apply_tgp.clicked.connect(self._apply_gpu_tgp)

        info_panel_layout.addWidget(self.tgp_slider)
        info_panel_layout.addWidget(self.tgp_value_label)
        info_panel_layout.addWidget(self.btn_apply_tgp)

        # Grubu ana düzene ekle
        layout.addWidget(info_panel)

        # V/F Curve
        curve_group = QGroupBox(" V/F Curve ")
        curve_layout = QVBoxLayout(curve_group)
        self.vf_widget = VFCurveWidget(parent=self)
        self.vf_widget.pointReleased.connect(self._on_point_released)
        self.vf_widget.pointDragged.connect(self._on_point_dragged)
        self.vf_widget.selectionChanged.connect(self._on_selection_changed)
        curve_layout.addWidget(self.vf_widget)

        bottom_buttons_layout = QHBoxLayout()
        bottom_buttons_layout.addStretch()
        self.btn_select_all = QPushButton("✅ Select All")
        self.btn_select_all.setObjectName("run_button")
        self.btn_select_all.setFixedHeight(22)
        self.btn_select_all.clicked.connect(self.vf_widget.select_all)
        bottom_buttons_layout.addWidget(self.btn_select_all)

        self.btn_reset_graph = QPushButton("↺ Reset Graph")
        self.btn_reset_graph.setObjectName("stop_button")
        self.btn_reset_graph.setFixedHeight(22)
        self.btn_reset_graph.clicked.connect(self._reset_graph_to_last_read)
        bottom_buttons_layout.addWidget(self.btn_reset_graph)
        curve_layout.addLayout(bottom_buttons_layout)
        layout.addWidget(curve_group, stretch=1)

        # Controls
        control_group = QGroupBox(" Controls ")
        control_layout = QHBoxLayout(control_group)

        core_label = SL("Core Offset:", color=C_GREY)
        self.core_offset_spin = QSpinBox()
        self.core_offset_spin.setRange(-500, 500)
        self.core_offset_spin.setValue(0)
        self.core_offset_spin.setSuffix(" MHz")
        self.core_offset_spin.setFixedHeight(25)
        self.core_offset_spin.setFixedWidth(120)
        self.core_offset_spin.valueChanged.connect(self._on_core_offset_changed)
        control_layout.addWidget(core_label)
        control_layout.addWidget(self.core_offset_spin)
        control_layout.addSpacing(20)

        mem_label = SL("Memory Offset:", color=C_GREY)
        self.mem_offset_spin = QSpinBox()
        self.mem_offset_spin.setRange(-1500, 1500)
        self.mem_offset_spin.setValue(0)
        self.mem_offset_spin.setSuffix(" MHz")
        self.mem_offset_spin.setFixedHeight(25)
        self.mem_offset_spin.setFixedWidth(100)
        control_layout.addWidget(mem_label)
        control_layout.addWidget(self.mem_offset_spin)
        control_layout.addSpacing(15)

        # VRAM locked-clock (max-frequency) lock — a separate NVML mechanism
        # from the offset above: pins the memory clock to a fixed [min, max]
        # MHz window instead of nudging the V/F curve (mirrors nvidia_oc's
        # --min-mem-clock/--max-mem-clock). 0 = not set on either spin box.
        vram_lock_label = SL("VRAM Lock:", color=C_GREY)
        control_layout.addWidget(vram_lock_label)

        self.vram_lock_min_spin = QSpinBox()
        self.vram_lock_min_spin.setRange(0, 20000)
        self.vram_lock_min_spin.setSpecialValueText("–")
        self.vram_lock_min_spin.setValue(0)
        self.vram_lock_min_spin.setSuffix(" MHz")
        self.vram_lock_min_spin.setFixedHeight(25)
        self.vram_lock_min_spin.setFixedWidth(85)
        self.vram_lock_min_spin.setToolTip("Min memory clock (MHz). Left at 0 → uses Max for both.")
        control_layout.addWidget(self.vram_lock_min_spin)

        dash_label = SL("–", color=C_GREY)
        control_layout.addWidget(dash_label)

        self.vram_lock_max_spin = QSpinBox()
        self.vram_lock_max_spin.setRange(0, 20000)
        self.vram_lock_max_spin.setSpecialValueText("–")
        self.vram_lock_max_spin.setValue(0)
        self.vram_lock_max_spin.setSuffix(" MHz")
        self.vram_lock_max_spin.setFixedHeight(25)
        self.vram_lock_max_spin.setFixedWidth(85)
        self.vram_lock_max_spin.setToolTip("Max memory clock (MHz) — the actual lock target.")
        control_layout.addWidget(self.vram_lock_max_spin)

        self.btn_vram_lock = QPushButton("🔒")
        self.btn_vram_lock.setObjectName("run_button")
        self.btn_vram_lock.setFixedHeight(22)
        self.btn_vram_lock.setFixedWidth(30)
        self.btn_vram_lock.setToolTip("Lock VRAM clock to Min–Max now (nvmlDeviceSetMemoryLockedClocks)")
        self.btn_vram_lock.clicked.connect(self._apply_vram_memlock)
        control_layout.addWidget(self.btn_vram_lock)

        self.btn_vram_unlock = QPushButton("🔓")
        self.btn_vram_unlock.setObjectName("stop_button")
        self.btn_vram_unlock.setFixedHeight(22)
        self.btn_vram_unlock.setFixedWidth(30)
        self.btn_vram_unlock.setToolTip("Unlock VRAM clock — return to driver/P-state control")
        self.btn_vram_unlock.clicked.connect(self._reset_vram_memlock)
        control_layout.addWidget(self.btn_vram_unlock)

        control_layout.addStretch()

        self.btn_read_curve = QPushButton("📥 Read Current Curve")
        self.btn_read_curve.setObjectName("run_button")
        self.btn_read_curve.setFixedHeight(22)
        self.btn_read_curve.clicked.connect(self._read_gpu_curve)
        control_layout.addWidget(self.btn_read_curve)

        self.btn_apply_offset = QPushButton("🚀 Apply Offsets")
        self.btn_apply_offset.setObjectName("apply_button")
        self.btn_apply_offset.setFixedHeight(22)
        self.btn_apply_offset.clicked.connect(self._apply_gpu_offsets)
        control_layout.addWidget(self.btn_apply_offset)

        self.btn_reset_curve = QPushButton("↺ Reset Curve")
        self.btn_reset_curve.setObjectName("stop_button")
        self.btn_reset_curve.setFixedHeight(22)
        self.btn_reset_curve.clicked.connect(self._reset_gpu_curve)
        control_layout.addWidget(self.btn_reset_curve)

        layout.addWidget(control_group)
        self.tabs.addTab(tab, "  🎮 GPU TUNING  ")

        # B3: The GPU timer only runs while the GPU tab is visible.
        # While on other tabs, the dGPU is allowed to drop into D3cold;
        # NVML/nvidia-smi fork overhead also drops to zero.
        self.gpu_info_timer = QTimer(self)
        self.gpu_info_timer.setInterval(2000)
        self.gpu_info_timer.timeout.connect(self._update_gpu_info)
        # Connect the tab-change signal (once __init__ finishes, _connect_tab_timers is called)
        self._gpu_tab_index = None   # _connect_tab_timers'ta doldurulacak

    # ─── GPU TUNING METHODS ─────────────────────────────────────────────

    def _apply_gpu_tgp(self):
        """Seçilen TGP değerini /usr/local/sbin/nvctgp betiği üzerinden uygular."""
        target_watt = self.tgp_slider.value()
        self._log(f"⚙️ GPU TGP {target_watt}W olarak ayarlanıyor...")

        # root_helper.py bu içeriği Python olarak çalıştıracağı için subprocess kullanıyoruz.
        script_content = f"""
import subprocess
import sys

try:
    # nvctgp <watt> komutunu çalıştır
    result = subprocess.run(['/usr/local/sbin/nvctgp', '{target_watt}'], capture_output=True, text=True)

    if result.returncode != 0:
        # Hata durumunda stderr veya stdout'u yakala ve root_helper'a WARNING olarak ilet
        error_msg = result.stderr.strip() if result.stderr else result.stdout.strip()
        print(f"WARNING: nvctgp hatası (Kod {{result.returncode}}): {{error_msg}}", file=sys.stderr)
        sys.exit(result.returncode)
    else:
        # Başarılı olduğunda çıktıyı logla
        print(f"OK: {{result.stdout.strip()}}")
except Exception as e:
    print(f"WARNING: nvctgp çalıştırılamadı: {{e}}", file=sys.stderr)
    sys.exit(1)
"""
        self._run_root_helper_command(
            {"op": "run_script_content", "content": script_content},
            f"GPU TGP başarıyla {target_watt}W olarak ayarlandı.",
            "GPU TGP ayarlanırken hata oluştu.",
            callback=None
        )

    def _recompute_display(self):
        if not self._default_points:
            return
        points = []
        for i, (v, base_freq) in enumerate(self._default_points):
            offset = self._point_offsets.get(i, 0)
            freq = base_freq + offset + self._core_offset
            freq = max(0, freq)
            points.append((v, int(freq)))
        self.vf_widget.update_points(points)
        self._on_selection_changed()

    def _update_core_offset_ui(self):
        if self._curve_modified:
            self.core_offset_spin.setEnabled(False)
            self.core_offset_spin.setRange(-501, 500)
            self.core_offset_spin.blockSignals(True)
            self.core_offset_spin.setValue(-501)
            self.core_offset_spin.blockSignals(False)
            self.core_offset_spin.setSpecialValueText("⚠ CURVE")
            self.core_offset_spin.setStyleSheet(
                f"QSpinBox {{ color:{C_STOP}; font-weight:bold; }}"
            )
        else:
            self.core_offset_spin.setEnabled(True)
            self.core_offset_spin.setSpecialValueText("")
            self.core_offset_spin.setRange(-500, 500)
            self.core_offset_spin.setStyleSheet("")

    def _apply_flatten(self):
        """Flatten from the selected index using the current (base + offset) frequency."""
        if not self._default_points:
            return
        start_idx = self._flatten_threshold
        if start_idx < 0 or start_idx >= len(self._default_points):
            return

        base_start_freq = self._default_points[start_idx][1]
        start_offset = self._point_offsets.get(start_idx, 0) + self._core_offset
        target_freq = base_start_freq + start_offset

        # Clear the offsets for points after start_idx (start_idx itself is preserved)
        for i in range(start_idx + 1, len(self._default_points)):
            self._point_offsets.pop(i, None)

        # Compute a new offset for the points after start_idx
        for i in range(start_idx + 1, len(self._default_points)):
            base_freq = self._default_points[i][1]
            offset = target_freq - base_freq - self._core_offset
            self._point_offsets[i] = int(offset)

        self._recompute_display()
        self._read_offsets = self._point_offsets.copy()
        self._read_core_offset = self._core_offset

    def _on_flatten_entered(self):
        val = self.limit_spin.value()
        self._flatten_threshold = val
        self._apply_flatten()
        self._curve_modified = True
        self._update_core_offset_ui()
        self._log(f"Flatten applied from index {val}")

    def _on_core_offset_changed(self, val):
        self._core_offset = val
        self._recompute_display()

    def _on_point_offset_spin_changed(self, val):
        # BUG FIX: this used to loop over self.vf_widget.selected_indices,
        # which can silently hold more than the one point the user thinks
        # is selected (e.g. a leftover multi-selection from Select All or a
        # Space-toggle). Looping over it meant a single point's offset edit
        # got written to every point still in that set, making it behave
        # like a second core offset. Point Offset now only ever touches the
        # single active point (current_index); use Select All + arrow keys
        # or Flatten for deliberate multi-point edits.
        idx = self.vf_widget.current_index
        if idx is None or idx < 0 or idx >= len(self._default_points):
            return
        if idx not in self.vf_widget.selected_indices:
            return
        self._point_offsets[idx] = val - self._core_offset
        self._curve_modified = True
        self._update_core_offset_ui()
        self._recompute_display()

    def _on_point_released(self, index, freq):
        base = self._default_base_freqs[index] if index < len(self._default_base_freqs) else 0
        offset = freq - base - self._core_offset
        if self._point_offsets.get(index, 0) != offset:
            self._point_offsets[index] = offset
            self._curve_modified = True
            self._update_core_offset_ui()
            self._recompute_display()

    def _sync_offsets_from_widget(self):
        if not self._default_points:
            return
        points = self.vf_widget.get_points()
        for i, (v, f) in enumerate(points):
            if i < len(self._default_base_freqs):
                base = self._default_base_freqs[i]
                self._point_offsets[i] = f - base - self._core_offset

    def _on_point_dragged(self, index, new_freq):
        if index == -1:
            self._sync_offsets_from_widget()
            self._curve_modified = True
            self._update_core_offset_ui()
            self._recompute_display()

    def _on_selection_changed(self):
        selected = self.vf_widget.selected_indices
        if selected:
            max_idx = max(selected)
            self.limit_spin.blockSignals(True)
            self.limit_spin.setValue(max_idx)
            self.limit_spin.blockSignals(False)

            idx = self.vf_widget.current_index if self.vf_widget.current_index in selected else next(iter(selected))
            v, f = self.vf_widget.points[idx]
            offset = self._point_offsets.get(idx, 0)
            total_offset = offset + self._core_offset

            self.point_index_label.setText(f"Selected: {len(selected)} pts")
            self.point_voltage_label.setText(f"Voltage: {v} mV")
            self.point_freq_label.setText(f"Freq: {f} MHz")
            self.point_offset_label.setText(f"Total Offset: {total_offset} MHz")

            self.point_offset_spin.setEnabled(True)
            self.point_offset_spin.blockSignals(True)
            self.point_offset_spin.setValue(total_offset)
            self.point_offset_spin.blockSignals(False)
        else:
            self.point_index_label.setText("Selected: -")
            self.point_voltage_label.setText("Voltage: - mV")
            self.point_freq_label.setText("Freq: - MHz")
            self.point_offset_label.setText("Offset: - MHz")
            self.point_offset_spin.setEnabled(False)
            self.point_offset_spin.blockSignals(True)
            self.point_offset_spin.setValue(0)
            self.point_offset_spin.blockSignals(False)

    def _reset_graph_to_last_read(self):
        if not self._read_offsets:
            self._core_offset = getattr(self, '_read_core_offset', 0)
            self._point_offsets = {}
            self.core_offset_spin.blockSignals(True)
            self.core_offset_spin.setValue(self._core_offset)
            self.core_offset_spin.blockSignals(False)
            self._curve_modified = False
            self._update_core_offset_ui()
            self._recompute_display()
            self.vf_widget.clear_selection()
            self.point_offset_spin.blockSignals(True)
            self.point_offset_spin.setValue(0)
            self.point_offset_spin.blockSignals(False)
            self._log("↺ Graph reset to last read values (core offset only).")
            return

        offsets = [self._read_offsets.get(i, 0) for i in range(127)]
        unique_offsets = set(offsets)
        if len(unique_offsets) == 1:
            core_val = unique_offsets.pop()
            self._point_offsets = self._read_offsets.copy()
            self._core_offset = core_val
            self.core_offset_spin.blockSignals(True)
            self.core_offset_spin.setValue(core_val)
            self.core_offset_spin.blockSignals(False)
            self._curve_modified = False
        else:
            self._point_offsets = self._read_offsets.copy()
            self._core_offset = 0
            self.core_offset_spin.blockSignals(True)
            self.core_offset_spin.setValue(0)
            self.core_offset_spin.blockSignals(False)
            self._curve_modified = True

        self._flatten_threshold = -1
        self.limit_spin.blockSignals(True)
        self.limit_spin.setValue(-1)
        self.limit_spin.blockSignals(False)
        self._update_core_offset_ui()
        self._recompute_display()
        self.vf_widget.clear_selection()
        self.point_offset_spin.blockSignals(True)
        self.point_offset_spin.setValue(0)
        self.point_offset_spin.blockSignals(False)
        self._log("↺ Graph reset to last read values.")

    # B2: cache the last update state
    _pb_last_state = None

    def _update_profile_buttons(self):
        """Updates the profile buttons based on active/inactive state.
        Only the outer border is colored; the content (icon, text, background) stays fixed.
        """
        if self._busy:
            return

        # B2: don't trigger a re-polish if the state hasn't changed
        state_key = (
            self._active_profile,
            self.current,
            self.edit_mode,
            self.edit_profile,
            self.gmode_active,
        )
        if state_key == self._pb_last_state:
            return
        self._pb_last_state = state_key

        # Determine the active profile
        active = self._active_profile.lower() if self._active_profile else ""
        if not active or active == "unknown":
            active = self.current.lower() if self.current else ""

        for btn_name, widgets in self.profile_buttons.items():
            frame = widgets["frame"]
            indicator = widgets["indicator"]

            # G-MODE special case
            if btn_name == "performance" and self.gmode_active:
                color = C_WHITE
                label_text = "G-MODE"
                is_edit = self.edit_mode and self.edit_profile == "gmode"
            else:
                style = self.PROFILE_STYLES.get(btn_name.lower(), {})
                color = style.get("color", C_GREY)
                label_text = style.get("label", btn_name.upper())
                is_edit = self.edit_mode and self.edit_profile == btn_name

            # Only update the text (background, color, font stay unchanged)
            widgets["label"].setText(label_text)

            if btn_name == "performance" and self.gmode_active:
                is_active = active in ("gmode", "performance")
            else:
                is_active = btn_name.lower() == active

            # Frame settings: only the border changes, content stays fixed
            if is_edit or is_active:
                # Active/edit: thick colored border
                frame.setStyleSheet(f"""
                    QFrame {{
                        background-color: transparent;
                        border: 2px solid {color};
                        border-radius: 6px;
                        padding: 0px;
                    }}
                    QFrame:hover {{
                        border: 2px solid {color};
                    }}
                """)
                indicator.setStyleSheet(f"""
                    background-color: {color};
                    border-bottom-left-radius: 4px;
                    border-bottom-right-radius: 4px;
                """)
            else:
                # Normal: ince gri border
                frame.setStyleSheet(f"""
                    QFrame {{
                        background-color: transparent;
                        border: 1px solid {C_BORDER};
                        border-radius: 6px;
                        padding: 0px;
                    }}
                    QFrame:hover {{
                        border: 1px solid {C_VDGREY};
                    }}
                """)
                indicator.setStyleSheet("background-color: transparent;")

    def _build_tab_extra_tools(self):
        """Extra Tools tab with game and system optimizations."""
        tab = QWidget()
        tab.setStyleSheet(f"background:{C_BG};")

        main_layout = QVBoxLayout(tab)
        main_layout.setSpacing(0)
        main_layout.setContentsMargins(0, 0, 0, 0)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setStyleSheet("QScrollArea { border: none; background: transparent; }")
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)

        container = QWidget()
        container.setStyleSheet(f"background:{C_BG};")
        container_layout = QVBoxLayout(container)
        container_layout.setSpacing(8)
        container_layout.setContentsMargins(12, 12, 12, 12)

        # ─── HEADER ──────────────────────────────────────────────────────
        title = SL("🎮 CUSTOM / G-MODE  ", bold=True, color=C_CYAN, size=10)
        title.setAlignment(Qt.AlignCenter)
        container_layout.addWidget(title)

        # ─── THP GROUP (Transparent Huge Pages) ────────────────────────
        thp_group = QGroupBox(" Transparent Huge Pages ")
        thp_group.setStyleSheet(f"""
            QGroupBox {{
                color: {C_ORANGE};
                border: 1px solid {C_BORDER};
                border-radius: 3px;
                margin-top: 5px;
                padding-top: 5px;
                font-size: 8pt;
                font-weight: bold;
            }}
            QGroupBox::title {{
                subcontrol-origin: margin;
                left: 6px;
                padding: 0 3px;
            }}
        """)
        thp_group.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        thp_group.setFixedHeight(150)
        thp_layout = QVBoxLayout(thp_group)
        thp_layout.setContentsMargins(8, 8, 8, 8)
        thp_layout.setSpacing(4)

        # enabled
        row1 = QHBoxLayout()
        row1.setSpacing(8)
        row1.addWidget(SL("enabled:", color=C_GREY, size=8))
        self.thp_enabled_combo = QComboBox()
        self.thp_enabled_combo.setFixedHeight(24)
        self.thp_enabled_combo.setMinimumWidth(130)
        self.thp_enabled_combo.addItems(["always", "madvise", "never"])
        row1.addWidget(self.thp_enabled_combo)
        row1.addStretch()
        thp_layout.addLayout(row1)

        # defrag
        row2 = QHBoxLayout()
        row2.setSpacing(8)
        row2.addWidget(SL("defrag:", color=C_GREY, size=8))
        self.thp_defrag_combo = QComboBox()
        self.thp_defrag_combo.setFixedHeight(24)
        self.thp_defrag_combo.setMinimumWidth(150)
        self.thp_defrag_combo.addItems(["always", "defer", "defer+madvise", "madvise", "never"])
        row2.addWidget(self.thp_defrag_combo)
        row2.addStretch()
        thp_layout.addLayout(row2)

        # shmem_enabled
        row3 = QHBoxLayout()
        row3.setSpacing(8)
        row3.addWidget(SL("shmem:", color=C_GREY, size=8))
        self.thp_shmem_combo = QComboBox()
        self.thp_shmem_combo.setFixedHeight(24)
        self.thp_shmem_combo.setMinimumWidth(130)
        self.thp_shmem_combo.addItems(["always", "never", "within_size"])
        row3.addWidget(self.thp_shmem_combo)
        row3.addStretch()
        thp_layout.addLayout(row3)

        # ─── PERFORMANCE MODE (G-MODE / OVERDRIVE) ──────────────────────
        perf_group = QGroupBox(" Performance Mode ")
        perf_group.setStyleSheet(f"""
            QGroupBox {{
                color: {C_ORANGE};
                border: 1px solid {C_BORDER};
                border-radius: 3px;
                margin-top: 5px;
                padding-top: 5px;
                font-size: 8pt;
                font-weight: bold;
            }}
            QGroupBox::title {{
                subcontrol-origin: margin;
                left: 6px;
                padding: 0 3px;
            }}
        """)
        perf_group.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        perf_group.setFixedHeight(150)  # same height as THP
        perf_layout = QVBoxLayout(perf_group)
        perf_layout.setContentsMargins(8, 8, 8, 8)
        perf_layout.setSpacing(4)

        # Left-align the combo and center it vertically
        combo_container = QHBoxLayout()
        # Don't add a stretch to left-align, just add the widget
        self.gmode_combo = QComboBox()
        self.gmode_combo.setFixedHeight(24)
        self.gmode_combo.setMinimumWidth(150)
        self.gmode_combo.addItems(["OVERDRIVE", "G-MODE"])
        self.gmode_combo.currentIndexChanged.connect(self._on_gmode_combo_changed)
        combo_container.addWidget(self.gmode_combo)
        combo_container.addStretch()  # leave space on the right, lean left
        perf_layout.addLayout(combo_container)

        # Put THP and Performance Mode side by side
        thp_perf_layout = QHBoxLayout()
        thp_perf_layout.setSpacing(8)
        thp_perf_layout.addWidget(thp_group, stretch=1)
        thp_perf_layout.addWidget(perf_group, stretch=1)
        container_layout.addLayout(thp_perf_layout)

        # ─── GAMING OPTIMIZATIONS GROUP ─────────────────────────────────
        gaming_group = QGroupBox(" Gaming Optimizations ")
        gaming_group.setStyleSheet(f"""
            QGroupBox {{
                color: {C_GREEN};
                border: 1px solid {C_BORDER};
                border-radius: 3px;
                margin-top: 5px;
                padding-top: 5px;
                font-size: 8pt;
                font-weight: bold;
            }}
            QGroupBox::title {{
                subcontrol-origin: margin;
                left: 6px;
                padding: 0 3px;
            }}
        """)
        gaming_layout = QVBoxLayout(gaming_group)
        gaming_layout.setContentsMargins(8, 8, 8, 8)
        gaming_layout.setSpacing(4)

        # Gaming settings (excluding THP) — tek kaynak: wrapper.GAMING_TUNABLES
        # (böylece bu liste ile capture/restore'un kullandığı liste asla
        # birbirinden sapmaz).
        self.gaming_settings = wrapper.GAMING_TUNABLES

        # Grid layout (2 columns)
        grid = QGridLayout()
        grid.setSpacing(8)
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setColumnStretch(0, 1)
        grid.setColumnStretch(1, 1)

        self.gaming_widgets = {}
        row_idx = 0
        col_idx = 0
        for key, info in self.gaming_settings.items():
            container_widget = QWidget()
            container_widget_layout = QHBoxLayout(container_widget)
            container_widget_layout.setContentsMargins(0, 0, 0, 0)
            container_widget_layout.setSpacing(6)

            cb = QCheckBox()
            cb.setChecked(True)
            cb.setFixedWidth(18)
            container_widget_layout.addWidget(cb)

            label = QLabel(key)
            label.setStyleSheet("color: #ebdbb2; font-size: 8pt;")
            label.setFixedWidth(240)
            container_widget_layout.addWidget(label)

            container_widget_layout.addStretch()

            current_val = QLabel("?")
            current_val.setStyleSheet("color: #928374; font-size: 8pt;")
            current_val.setFixedWidth(100)
            current_val.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
            container_widget_layout.addWidget(current_val)

            rec_label = QLabel(f"→ {info['recommended']}")
            rec_label.setStyleSheet("color: #8ec07c; font-size: 8pt;")
            rec_label.setFixedWidth(120)
            rec_label.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
            container_widget_layout.addWidget(rec_label)

            grid.addWidget(container_widget, row_idx, col_idx, Qt.AlignVCenter)

            self.gaming_widgets[key] = {
                "checkbox": cb,
                "current_label": current_val,
                "info": info
            }

            col_idx += 1
            if col_idx >= 2:
                col_idx = 0
                row_idx += 1

        gaming_layout.addLayout(grid)

        container_layout.addWidget(gaming_group)

        # ─── BUTTONS (Refresh, Save, Defaults) ──────────────────────────
        btn_row = QHBoxLayout()
        btn_row.setSpacing(6)
        btn_row.addStretch()

        self.gaming_refresh_btn = QPushButton("↻ Refresh")
        self.gaming_refresh_btn.setObjectName("run_button")
        self.gaming_refresh_btn.setFixedHeight(24)
        self.gaming_refresh_btn.clicked.connect(lambda: self._refresh_gaming_status(elevated=True))
        btn_row.addWidget(self.gaming_refresh_btn)

        self.gaming_save_btn = QPushButton("💾 Save")
        self.gaming_save_btn.setObjectName("save_button")
        self.gaming_save_btn.setFixedHeight(24)
        self.gaming_save_btn.clicked.connect(self._save_gaming_settings)
        btn_row.addWidget(self.gaming_save_btn)

        self.gaming_restore_btn = QPushButton("↺ Defaults")
        self.gaming_restore_btn.setObjectName("stop_button")
        self.gaming_restore_btn.setFixedHeight(24)
        self.gaming_restore_btn.clicked.connect(self._restore_gaming_defaults)
        btn_row.addWidget(self.gaming_restore_btn)

        container_layout.addLayout(btn_row)

        scroll.setWidget(container)
        main_layout.addWidget(scroll)

        self.tabs.addTab(tab, "  🛠️ CUSTOM / GMODE  ")

        # Load status (elevated=False: never prompt for a password just from
        # opening the app; the few root-only values show "(needs root)"
        # until the user clicks Refresh or Apply)
        self._refresh_thp_status()
        self._refresh_gaming_status(elevated=False)
        for widgets in self.gaming_widgets.values():
            widgets["checkbox"].setChecked(False)
        # Update the G-MODE state
        self._update_gmode_combo_from_module()

    # ─── CPU CORE ISOLATION (CCX/CCD split, redirect-tasks/*.sh) ────────
    # K1 düzeltmesi: _run_root_bash_script ve _run_root_command burada
    # tamamen kaldırıldı. Her ikisi de /tmp içine 0o755 (dünya-okunur)
    # geçici bir Python wrapper yazıp pkexec ile root olarak çalıştırıyordu
    # — öngörülebilir yol + TOCTOU = yerel yetki yükseltme riski. Ayrıca
    # bu, root_helper.py'deki güvenli apply_cpu_isolation/revert_cpu_isolation
    # op'larıyla çift bir yol oluşturuyordu. Tüm çağrılar artık
    # _run_root_helper_command üzerinden ilgili root_helper op'larına
    # (apply_cpu_isolation, revert_cpu_isolation, run_script_content, ...)
    # yönlendiriliyor.

    def _on_isolation_output(self):
        if self.isolation_process is None:
            return
        data = self.isolation_process.readAllStandardOutput().data().decode(errors="replace")
        if data:
            self.isolation_output += data
            for line in data.strip().splitlines():
                self._log(line)

    def _on_isolation_error(self):
        if self.isolation_process is None:
            return
        data = self.isolation_process.readAllStandardError().data().decode(errors="replace")
        if data:
            for line in data.strip().splitlines():
                self._log(f"ERROR: {line}")

    def _on_isolation_finished(self, exitCode, exitStatus, success_msg, error_msg, callback):
        ok = (exitCode == 0 and exitStatus == QProcess.NormalExit)
        if ok:
            self._log(f"✅ {success_msg}")
        else:
            self._log(f"❌ {error_msg} (exit code: {exitCode})")
        if self.isolation_process is not None:
            self.isolation_process.deleteLater()
            self.isolation_process = None
        self.isolation_output = ""
        if callback:
            callback(ok)

    def _isolation_active(self) -> bool:
        """Isolation is considered active if the theGood/theUgly cgroups exist."""
        return os.path.isdir("/sys/fs/cgroup/theGood") or os.path.isdir("/sys/fs/cgroup/theUgly")

    def _refresh_isolation_status(self):
        if not hasattr(self, "isolation_status_label"):
            return
        if self._isolation_active():
            self.isolation_status_label.setText("● ACTIVE")
            self.isolation_status_label.setStyleSheet(f"color: {C_GREEN}; font-weight: bold;")
        else:
            self.isolation_status_label.setText("○ Inactive")
            self.isolation_status_label.setStyleSheet(f"color: {C_GREY};")

    def _on_isolation_apply_clicked(self):
        launcher = self.isolation_launcher_edit.text().strip() or "lutris"
        self.isolation_apply_btn.setEnabled(False)
        self.isolation_revert_btn.setEnabled(False)
        self._log(f"🔒 Applying CPU isolation (launcher: {launcher})...")

        self._run_root_helper_command(
            {"op": "apply_cpu_isolation", "launcher": launcher},
            success_msg="CPU isolation applied (theGood/theUgly).",
            fail_msg="CPU isolation could not be applied.",
            callback=self._reenable_isolation_buttons,
        )

    def _on_isolation_revert_clicked(self):
        self.isolation_apply_btn.setEnabled(False)
        self.isolation_revert_btn.setEnabled(False)
        self._log("♻ Reverting CPU isolation...")

        self._run_root_helper_command(
            {"op": "revert_cpu_isolation"},
            success_msg="CPU isolation reverted (theGood/theUgly removed).",
            fail_msg="CPU isolation could not be reverted.",
            callback=self._reenable_isolation_buttons,
        )

    def _reenable_isolation_buttons(self, ok):
        # D10: shared apply/revert completion callback
        self.isolation_apply_btn.setEnabled(True)
        self.isolation_revert_btn.setEnabled(True)
        self._refresh_isolation_status()

    # ─── ROOT COMMANDS ──────────────────────────────────────────────────
    # K1 düzeltmesi: _run_root_command (ve _on_root_output/_on_root_error/
    # _on_root_finished) burada tamamen kaldırıldı. Bu metod GUI'nin ürettiği
    # bir Python script'ini doğrudan pkexec ile root olarak çalıştırıyordu;
    # tek çağrı yeri olan _script() artık _run_root_helper_command +
    # root_helper'ın run_script_content op'unu kullanıyor (bkz. yukarıdaki
    # not). self.root_process yalnızca uygulama kapanışındaki eski temizlik
    # kontrolü için (zararsız) korunuyor.

    # ─── PROFILE OPERATIONS ────────────────────────────────────────────
    def _load_and_apply_profile(self, name):
        if self._busy:
            return

        # Write the extra settings into the scripts (use the most recently saved extra settings)
        if name in ("gmode", "custom") and self.gmode_active:
            self._update_scripts_with_extra(self.extra_settings)

        self._load_profile(name, apply=True)

        # NOTE: _active_profile and _update_profile_buttons are not called here,
        # because _busy becomes true while _apply_profile is running.
        # The highlight is updated via the _applied signal.

        if name in ("gmode", "custom") and self.gmode_active:
            self._log(f"ℹ️ {name} profile includes extra settings.")

    def _load_profile(self, name, apply=True):
        """Loads the profile. Applies it if apply=True (normal mode), otherwise just displays it."""
        if not name:
            return
        self.current = name
        self.dirty = False

        try:
            data = wrapper.load_profile(name)
        except Exception as e:
            self._log(f"[ERROR] {e}")
            return

        # Update button highlights
        self._update_profile_buttons()

        # Clear and rebuild the interface
        self._clear_layout(self.s_layout)
        self.config = {}
        self.cores = []

        # Power Limits
        g_pow = QGroupBox(" Power, Current & Thermal Limits ")
        g_pow.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        gl_pow = QGridLayout(g_pow)
        gl_pow.setSpacing(6)
        gl_pow.setContentsMargins(8, 12, 8, 8)

        keys = [
            ("stapm_limit_mw", "STAPM Limit (mW)"),
            ("fast_limit_mw", "Fast PPT Limit (mW)"),
            ("slow_limit_mw", "Slow PPT Limit (mW)"),
            ("tctl_temp_c", "Temp Target (°C)"),
            ("vrm_current_ma", "VRM Current (mA)"),
        ]
        for r, (k, lbl_txt) in enumerate(keys):
            val = data.get(k, "")
            lb = SL(lbl_txt, color=C_GREEN, size=8)
            lb.setFixedWidth(155)
            gl_pow.addWidget(lb, r, 0, Qt.AlignLeft | Qt.AlignVCenter)
            e = SE(str(val), width=65)
            e.textChanged.connect(self._mark)
            gl_pow.addWidget(e, r, 1, Qt.AlignLeft | Qt.AlignVCenter)
            self.config[k] = e
        self.s_layout.addWidget(g_pow, stretch=1)

        # Fan Boost
        g_fan = QGroupBox(" Fan Boost (0–100%) ")
        g_fan.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        gl_fan = QGridLayout(g_fan)
        gl_fan.setSpacing(6)
        gl_fan.setContentsMargins(8, 12, 8, 8)

        for r, (k, lbl_txt) in enumerate([
            ("fan_boost_1", "CPU Fan"), ("fan_boost_2", "GPU Fan"),
            ("fan_boost_3", "Mid Fan"), ("fan_boost_4", "Side Fan")
        ], start=1):
            lb = SL(lbl_txt, color=C_GREEN, size=8)
            lb.setFixedWidth(155)
            gl_fan.addWidget(lb, r, 0, Qt.AlignLeft | Qt.AlignVCenter)
            e = SE(str(data.get(k, 0)), width=65)
            e.textChanged.connect(self._mark)
            gl_fan.addWidget(e, r, 1, Qt.AlignLeft | Qt.AlignVCenter)
            self.config[k] = e
        self.s_layout.addWidget(g_fan, stretch=1)

        # Curve Optimizer
        self._load_co(data)

        # Update the state of the controls
        self._update_controls_state()

        # Apply operation (in normal mode)
        if apply and not self.edit_mode:
            self._apply_profile(name)

    def _load(self, name):
        self._load_profile(name, apply=True)

    def _load_co(self, data):
        v = data.get("coall", "")
        if self._coall_entry:
            self._coall_entry.blockSignals(True)
            self._coall_entry.setText(str(v) if v is not None else "")
            self._coall_entry.blockSignals(False)
        self.config["coall"] = self._coall_entry
        self.cores = data.get("cores", [])
        if self.cores:
            self._populate_co(self.cores)

    def _mark(self):
        self.dirty = True
        pass

    def _safe_log_append(self, msg):
        """
        This method is triggered by log_signal and ALWAYS runs on the main GUI thread.
        Prevents duplicate printing and auto-scrolls the box down.
        """
        if hasattr(self, 'log_box') and self.log_box:
            # We guarantee it's printed to the screen exactly once
            self.log_box.append(msg)

            # Auto-scroll down
            cur = self.log_box.textCursor()
            cur.movePosition(QTextCursor.MoveOperation.End)
            self.log_box.setTextCursor(cur)

    def _log(self, msg):
        """
        Safely sends the log message to the GUI via a signal
         and writes it to the cache file in the background.
        """
        # 1. GUI update (the signal mechanism is thread-safe)
        if hasattr(self, 'log_signal'):
            # We don't touch log_box directly, we just emit a signal.
            # This way, no matter which thread the call comes from, Qt handles it on the main thread.
            self.log_signal.emit(msg)
        elif hasattr(self, 'log_box'):
            # If the signal isn't connected yet, print directly as a fallback
            self._safe_log_append(msg)
        else:
            print(msg)

        # 2. File write operation (utf-8 is required for special characters and emojis)
        try:
            log_file = os.path.expanduser("~/.cache/ryzenadj_gui.log")
            os.makedirs(os.path.dirname(log_file), exist_ok=True)
            with open(log_file, "a", encoding="utf-8") as f:
                timestamp = time.strftime("%Y-%m-%d %H:%M:%S")
                f.write(f"[{timestamp}] {msg}\n")
        except Exception:
            pass

    def _gather(self):
        data = {}
        for k, w in self.config.items():
            if k == "coall":
                v = w.text().strip()
                data[k] = int(v) if v else None
                continue
            v = w.text().strip()
            if v:
                try:
                    data[k] = int(v)
                except Exception:
                    pass
        has_all = data.get("coall") is not None
        core_list = []
        for orig in (self.cores if isinstance(self.cores, list) else []):
            cid = orig.get("id", 0)
            fe = next((e for wid, e, _ in self._co_core_wgts if wid == cid), None)
            if fe and not has_all:
                try:
                    off = int(fe.text().strip())
                except Exception:
                    off = 0
            else:
                off = orig.get("coper", 0)
            core_list.append({
                "id": orig.get("id", cid),
                "ccd": orig.get("ccd", 0),
                "ccx": orig.get("ccx", 0),
                "core": orig.get("core", 0),
                "coper": off
            })
        if core_list:
            data["cores"] = core_list
        return data

# ─── 1. ANA SAVE BUTONU ──────────────────────────────────────────
    def _save(self):
        if not self.current:
            return
        # First gather the data
        data = self._gather()
        name_clean = self.current.replace("● ", "")

        # If G-MODE is active and it's performance, redirect to gmode
        if name_clean == "performance" and self.gmode_active:
            name_clean = "gmode"
            data["extra"] = self.extra_settings
        elif name_clean in ["gmode", "custom"]:
            data["extra"] = self.extra_settings

        # Update the script (no root needed, written with user permission)
        try:
            wrapper.write_shell_script(name_clean, data)
        except Exception as e:
            self._log(f"⚠️ Script could not be updated: {e}")

        json_str = json.dumps(data, indent=2)

        # Create the files so the old file-deletion logic in the callback doesn't error out
        with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False) as tf:
            tf.write(json_str)
            tmp_path = tf.name

        with tempfile.NamedTemporaryFile(mode='w', suffix='.py', delete=False) as tf:
            tf.write("# Direct execution disabled for safety.")
            script_path = tf.name
        os.chmod(script_path, 0o755)

        # K4: Artık ham bir dosya yolu göndermiyoruz. root_helper.py
        # yalnızca doğrulanmış bir profil `name`'i kabul ediyor ve hedef
        # yolu kendi whitelist edilmiş dizininde inşa ediyor; bu sayede
        # GUI ele geçirilse bile keyfi bir yola root olarak yazılamaz.
        payload = {
            "op": "save_power_profile",
            "name": name_clean,
            "content": json_str
        }

        self._run_root_helper_command(
            payload,
            f"'{name_clean}.json' saved.",
            f"Failed to save '{name_clean}.json'.",
            callback=lambda out: self._on_save_finished("OK\n", tmp_path, script_path, name_clean)
        )

    # ─── 2. GPU TUNING: SAVE PROFILE AS ──────────────────────────────
    def _save_profile_as(self):
        name, ok = QInputDialog.getText(self, "Profile Name", "Enter profile name:", text="custom")
        if not ok or not name.strip():
            self._log("❌ No profile name entered, cancelled.")
            return

        name = name.strip()
        profile_data = self._gather_current_profile_data()
        profile_data["name"] = name
        json_str = json.dumps(profile_data, indent=2)

        payload = {
            "op": "write_nvcurve_profile",
            "name": name,
            "content": json_str,
        }

        # The callback runs on the main thread (via QTimer.singleShot).
        def on_saved(_):
            self._refresh_profile_list()
            idx = self._find_profile_index(name)
            if idx >= 0:
                self.profile_combo.setCurrentIndex(idx)

        # _run_root_helper_command → pkexec root_helper.py (stdin) →
        # Same polkit action as G-MODE, the auth cache is shared, the UI isn't blocked.
        self._run_root_helper_command(
            payload,
            f"Profile '{name}' saved.",
            f"Profile '{name}' could not be saved.",
            callback=on_saved,
        )

    # ─── 3. GPU TUNING: READ CURRENT CURVE ───────────────────────────
    def _read_gpu_curve(self):
        self.btn_read_curve.setEnabled(False)
        self.btn_read_curve.setText("⏳ Reading...")
        project_dir = os.path.dirname(os.path.abspath(__file__))

        payload = {
            "op": "read_gpu_curve",
            "project_dir": project_dir
        }

        self._run_root_helper_command(
            payload,
            "Curve read successfully.",
            "Curve read failed.",
            callback=self._on_curve_read_finished
        )

    # ─── 4. GPU TUNING: APPLY OFFSETS ───────────────────────────────────
    def _apply_gpu_offsets(self):
        profile_name = "ppm"

        if not self._default_points:
            self._log("❌ No curve loaded yet — read the current curve or load a profile first.")
            return

        # WYSIWYG: the deltas we send to hardware are derived directly from
        # what's currently drawn in the V/F graph, not from the
        # _point_offsets/_core_offset bookkeeping.
        #
        # BUG FIX: this used to loop over self._gpu_indices, a list only
        # ever populated by an actual hardware read (Read Current Curve /
        # after an apply/reset). Loading a saved profile from the combo
        # never touched it, so if a profile was loaded first and a point
        # was then dragged on the graph, self._gpu_indices was still empty
        # (or stale) and the edit was silently dropped — the previously
        # saved profile's values effectively stayed in effect on hardware
        # instead of the graph's edited curve. Reading straight from
        # self.vf_widget.get_points() means the graph is always the single
        # source of truth for what gets written.
        graph_points = self.vf_widget.get_points()
        offsets_to_apply = {}
        for i, (_, freq) in enumerate(graph_points):
            if i >= len(self._default_base_freqs):
                break
            base = self._default_base_freqs[i]
            delta = int(round(freq)) - base
            if delta != 0:
                offsets_to_apply[i] = delta * 1000

        mem_off = self.mem_offset_spin.value()
        # EXPERIMENT: previously this also wrote offsets_to_apply[131]/[132]
        # (NvAPI ClockBoostTable memory points) with the SAME delta that goes
        # into mem_offset_mhz below (NVML). Two different APIs poking the
        # same underlying VF curve/offset in one apply is the likely cause
        # of the P0→P2 pstate confusion observed when combining a curve edit
        # with a memory offset. Testing NVML-only (mem_offset_mhz) now;
        # points 131/132 are intentionally NOT written here anymore.
        #
        # NVML on Linux is known to apply only half of the requested
        # mem_offset_mhz (GDDR real-clock vs effective-data-rate reporting —
        # same phenomenon noted in LACT's issue #486 thread). The spin box
        # represents the effective MHz the user wants; send double that to
        # NVML so the actually-applied clock matches what's shown here.
        mem_off_nvml = mem_off * 2

        vram_lock_max = self.vram_lock_max_spin.value()
        vram_lock_min = self.vram_lock_min_spin.value()
        if vram_lock_max != 0 and vram_lock_min == 0:
            vram_lock_min = vram_lock_max

        if not offsets_to_apply and mem_off == 0 and vram_lock_max == 0:
            self._log("No offsets to apply.")
            return

        profile_data = {
            "name": profile_name,
            "gpu_name": "NVIDIA GeForce RTX 4080 Laptop GPU",
            "curve_deltas": {str(k): v for k, v in offsets_to_apply.items()},
            # FIX: we used to send 0 when mem_off=0; on the nvcurve side the
            # "is not None" check treats 0 as valid too and makes an
            # unnecessary NVML mem-offset call, which crashed the whole
            # apply with a permission error on some drivers.
            # mem_offset_mhz stores the RAW value sent to NVML (already
            # doubled) — this is what any other apply path (CLI, autoload)
            # sends straight to NVML, so it must already include the 2x
            # compensation, not the "effective" box value.
            "mem_offset_mhz": mem_off_nvml if mem_off != 0 else None,
            "power_limit_w": None,
            "mem_locked_min_mhz": vram_lock_min if vram_lock_max != 0 else None,
            "mem_locked_max_mhz": vram_lock_max if vram_lock_max != 0 else None,
        }


        project_dir = os.path.dirname(os.path.abspath(__file__))

        payload = {
            "op": "apply_gpu_offsets",
            "project_dir": project_dir,
            "profile_name": profile_name,
            "profile_data": profile_data
        }

        self._run_root_helper_command(
            payload,
            f"Profile '{profile_name}' applied successfully.",
            f"Failed to apply profile '{profile_name}'.",
            callback=self._after_apply_read
        )

    # ─── 5. GPU TUNING: RESET CURVE ─────────────────────────────────────
    def _reset_gpu_curve(self):
        self.btn_reset_curve.setEnabled(False)
        self.btn_reset_curve.setText("⏳ Resetting...")
        project_dir = os.path.dirname(os.path.abspath(__file__))

        payload = {
            "op": "reset_gpu_curve",
            "project_dir": project_dir
        }

        self._run_root_helper_command(
            payload,
            "Reset successful.",
            "Reset failed.",
            callback=self._after_reset_read
        )
        self._point_offsets = {}
        self._core_offset = 0
        self.core_offset_spin.setValue(0)
        self.mem_offset_spin.setValue(0)
        self.vram_lock_min_spin.setValue(0)
        self.vram_lock_max_spin.setValue(0)

    # ─── GPU TUNING: VRAM LOCKED CLOCKS (max-frequency lock) ─────────────
    # Separate NVML mechanism from the Memory Offset spin above — pins the
    # memory clock to a fixed [min, max] MHz window instead of nudging the
    # V/F curve (see nvcurve hal/limits.py set_mem_locked_clocks / nvidia_oc's
    # --min-mem-clock/--max-mem-clock). Applied immediately, independent of
    # the curve Apply/Reset buttons.
    def _apply_vram_memlock(self):
        max_mhz = self.vram_lock_max_spin.value()
        min_mhz = self.vram_lock_min_spin.value()
        if max_mhz == 0:
            self._log("⚠️ Enter a Max MHz value first (Min defaults to Max if left at 0).")
            return
        if min_mhz == 0:
            min_mhz = max_mhz
        if min_mhz > max_mhz:
            self._log("⚠️ VRAM Lock: Min cannot be greater than Max.")
            return

        self.btn_vram_lock.setEnabled(False)
        project_dir = os.path.dirname(os.path.abspath(__file__))

        payload = {
            "op": "set_vram_memlock",
            "project_dir": project_dir,
            "min_mhz": min_mhz,
            "max_mhz": max_mhz,
        }

        def done(_output):
            self.btn_vram_lock.setEnabled(True)

        self._run_root_helper_command(
            payload,
            f"VRAM locked to {min_mhz}–{max_mhz} MHz.",
            "Failed to lock VRAM clock.",
            callback=done,
        )

    def _reset_vram_memlock(self):
        self.btn_vram_unlock.setEnabled(False)
        project_dir = os.path.dirname(os.path.abspath(__file__))

        payload = {
            "op": "reset_vram_memlock",
            "project_dir": project_dir,
        }

        def done(_output):
            self.btn_vram_unlock.setEnabled(True)

        self._run_root_helper_command(
            payload,
            "VRAM clock unlocked.",
            "Failed to unlock VRAM clock.",
            callback=done,
        )

    def _on_save_finished(self, output, tmp_path, script_path, name_clean):
        try:
            os.remove(tmp_path)
            os.remove(script_path)
        except Exception:
            pass
        if output is not None:
            self.dirty = False
            self._log(f"✔ '{name_clean}.json' saved.")

    def _apply_profile(self, name: str):
        """Made the profile-apply operation crash-safe (try-except)."""
        if self._busy:
            return
        self._busy = True
        self.btn_save.setEnabled(False)
        try:
            # Boot-defaults: custom/gmode'a girmeden ÖNCE (henüz bu ayarlar
            # değiştirilmemişken) mevcut değerleri yakala; sade bir profile
            # dönülüyorsa apply'dan SONRA o değerlere geri dön. Her ikisi de
            # bu önyükleme için zaten yapıldıysa/gerek yoksa no-op'tur.
            if name in wrapper.TUNING_PROFILES:
                wrapper.ensure_boot_defaults_captured()

            self._log(f"Applying {name} from GUI...")
            success = wrapper.apply_profile(name)
            if success:
                tray_notified = wrapper.set_active_profile_state(name)
                self.current = name
                self._active_profile = name
                self._update_profile_buttons()
                self._log(f"✅ {name} profile applied.")

                if name in wrapper.SIMPLE_PROFILES:
                    wrapper.restore_boot_defaults()
                    self._log(f"↩ Restored boot-time defaults for gaming/THP tunables.")

                # Bildirim popup'ı yalnızca tray ÇALIŞMIYORSA burada
                # gösterilir. Tray çalışıyorsa write_active_profile()
                # zaten ona push bildirimi gönderdi ve tray kendi
                # notify-send'ini gösterecek (bkz. ryzenadj_tray.py::
                # _on_profile_pushed) — iki yerden birden göstermek
                # aynı bildirimi ikiletiyordu.
                if not tray_notified:
                    try:
                        subprocess.run(
                            ["notify-send", "--app-name=RyzenAdj", "--icon=preferences-system-power",
                             "Profile Applied", f"Switched to {name}"],
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=1
                        )
                    except Exception:
                        pass
            else:
                self._log(f"❌ Failed to apply {name}.")
                try:
                    subprocess.run(
                        ["notify-send", "--app-name=RyzenAdj", "--icon=dialog-error",
                         "Apply Failed", f"Could not apply {name}"],
                        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=1
                    )
                except Exception:
                    pass
        except Exception as e:
            self._log(f"❌ Error applying {name}: {e}")
        finally:
            self._busy = False
            self.btn_save.setEnabled(True)

    def _check_watcher(self):
        """Watchdog callback: protects the watcher and also prevents the icon from disappearing."""
        try:
            # To refresh the icon over DBus when the KDE Plasma panel crashes/restarts:
            if hasattr(self, "tray"):
                if not self.tray.isVisible():
                    self.tray.show()
                # Alternatively, in very rare cases the icon may still drop even if it appears visible,
                # but the isVisible check covers the general cases.
        except Exception as e:
            log(f"Watchdog tray check error: {e}")

        if not self.watcher.isRunning():
            log("Watchdog: watcher thread died, restarting...")
            self.watcher.deleteLater()
            self._start_watcher()

    # To make icon loading redundant (with a fallback), inside __init__:
    # (You can update the existing icon-loading logic like this)
    def _init_icon(self):
        icon_path = SCRIPT_DIR / "Alien.png"
        if icon_path.exists():
            self.tray.setIcon(QIcon(str(icon_path)))
        else:
            # 3 different popular power icon themes are tried, since this can vary by distro
            for theme_name in ["preferences-system-power", "power-profile-balanced", "battery", "utilities-terminal"]:
                icon = QIcon.fromTheme(theme_name)
                if not icon.isNull():
                    self.tray.setIcon(icon)
                    break

    def _applied(self, ok, name):
        self._busy = False
        self.btn_save.setEnabled(True)
        self._log(f"✔ {name} active." if ok else f"✘ {name} failed.")
        self._update_profile_buttons()
        self._update_controls_state()

    def _script(self):
        if not self.current:
            return
        # Create the script
        data = self._gather()
        name_clean = self.current.replace("● ", "")
        # Get the script content from the wrapper
        script_content = wrapper._build_shell_script_content(name_clean, data)

        # Bu script, kalıcı ve sistem genelinde erişilebilir olması için
        # root_helper.py'nin whitelist edilmiş VAR_SCRIPTS_DIR'ine
        # (/var/lib/ryzenadj-gui/scripts) yazılıyor. İçerik doğrudan
        # "write_activation_script" op'una gönderiliyor; ara dosya veya
        # dolaylı "script çalıştırıp kopyala" hilesi yok (eskiden burada
        # hem yanlış bir hedef dizin — wrapper.PROFILES_DIR — kullanılıyor
        # hem de gereksiz bir run_script_content dolambacı vardı).
        self._run_root_helper_command(
            {"op": "write_activation_script", "name": name_clean, "content": script_content},
            f"Script generated for {name_clean}",
            f"Failed to generate script for {name_clean}",
            callback=lambda out: self._on_script_finished(out)
        )

    def _on_script_finished(self, output):
        pass

    # ─── GPU PROFILE SAVE / LOAD ──────────────────────────────────────
    _DEFAULT_MARK = " ★"

    def _strip_default_mark(self, text: str) -> str:
        return text[: -len(self._DEFAULT_MARK)] if text.endswith(self._DEFAULT_MARK) else text

    def _find_profile_index(self, name: str) -> int:
        for i in range(self.profile_combo.count()):
            if self._strip_default_mark(self.profile_combo.itemText(i)) == name:
                return i
        return -1

    def _refresh_profile_list(self):
        profiles_dir = "/etc/nvcurve/profiles"
        try:
            if not os.path.exists(profiles_dir):
                self._log(f"⚠️ Profile directory does not exist: {profiles_dir}")
                return
            files = [f for f in os.listdir(profiles_dir) if f.endswith('.json')]
            files.sort()
            default_name = self._read_default_gpu_profile_name()
            self.profile_combo.blockSignals(True)
            self.profile_combo.clear()
            for f in files:
                name = f.replace('.json', '')
                label = f"{name}{self._DEFAULT_MARK}" if name == default_name else name
                self.profile_combo.addItem(label, f)
            idx = self._find_profile_index('ppm')
            if idx >= 0:
                self.profile_combo.setCurrentIndex(idx)
            elif self.profile_combo.count() > 0:
                self.profile_combo.setCurrentIndex(0)
            self.profile_combo.blockSignals(False)
        except Exception as e:
            self._log(f"⚠️ Could not read the profile list: {e}")

    def _on_profile_selected(self, index):
        if index < 0:
            return
        profile_name = self._strip_default_mark(self.profile_combo.currentText())
        if not profile_name:
            return
        profile_path = f"/etc/nvcurve/profiles/{profile_name}.json"
        try:
            with open(profile_path, 'r') as f:
                data = json.load(f)
            self._apply_profile_to_ui(data, profile_name)
        except FileNotFoundError:
            self._log(f"❌ Profile file not found: {profile_path}")
        except Exception as e:
            self._log(f"❌ Error while loading profile: {e}")

    def _apply_profile_to_ui(self, data, profile_name=""):
        if not self._default_points:
            self._default_points = [(700 + i * 4, 1500 + i * 2) for i in range(127)]
            self._default_base_freqs = [f for _, f in self._default_points]

        curve_deltas = data.get('curve_deltas', {})
        mem_offset_raw = data.get('mem_offset_mhz', 0)

        if mem_offset_raw != 0:
            # Stored value is the raw, already-doubled NVML value (see
            # _apply_gpu_offsets/_gather_current_profile_data) — halve it
            # back for display so the box shows the effective MHz.
            mem_offset = mem_offset_raw // 2
        elif '131' in curve_deltas:
            mem_offset = curve_deltas['131'] // 1000
        elif '132' in curve_deltas:
            mem_offset = curve_deltas['132'] // 1000
        else:
            mem_offset = 0

        new_offsets = {}
        for i in range(127):
            val_khz = curve_deltas.get(str(i), 0)
            new_offsets[i] = val_khz // 1000

        all_vals = list(new_offsets.values())
        if all_vals and all(v == all_vals[0] for v in all_vals):
            core_val = all_vals[0]
            self._core_offset = core_val
            self._point_offsets = {}
        else:
            self._core_offset = 0
            self._point_offsets = new_offsets

        self.core_offset_spin.blockSignals(True)
        self.core_offset_spin.setValue(self._core_offset)
        self.core_offset_spin.blockSignals(False)

        self.mem_offset_spin.blockSignals(True)
        self.mem_offset_spin.setValue(mem_offset)
        self.mem_offset_spin.blockSignals(False)

        self.vram_lock_min_spin.blockSignals(True)
        self.vram_lock_max_spin.blockSignals(True)
        self.vram_lock_min_spin.setValue(data.get('mem_locked_min_mhz') or 0)
        self.vram_lock_max_spin.setValue(data.get('mem_locked_max_mhz') or 0)
        self.vram_lock_min_spin.blockSignals(False)
        self.vram_lock_max_spin.blockSignals(False)

        self._read_core_offset = self._core_offset
        self._read_offsets = self._point_offsets.copy() if self._point_offsets else {}

        self._flatten_threshold = -1
        self.limit_spin.blockSignals(True)
        self.limit_spin.setValue(-1)
        self.limit_spin.blockSignals(False)

        self._curve_modified = False
        self._update_core_offset_ui()

        # BUG FIX: this used to call _reset_graph_to_last_read(), which was
        # written for a different purpose (resyncing the graph to the last
        # actual hardware read after Apply/Read/Reset Curve) and re-derives
        # state from self._read_offsets with its own branching. Loading a
        # profile now does its own explicit, unconditional reset instead:
        # clear any leftover selection and redraw strictly from what was
        # just computed above, so previously unapplied graph edits (and any
        # stray multi-selection) never carry over between profiles.
        self.vf_widget.clear_selection()
        self._recompute_display()
        self.point_offset_spin.blockSignals(True)
        self.point_offset_spin.setValue(0)
        self.point_offset_spin.blockSignals(False)
        self._log(f"✅ Profile applied to UI: {profile_name} (Core: {self._core_offset}, Mem: {mem_offset})")

    def _gather_current_profile_data(self):
        offsets = {}
        for i in range(127):
            offset = self._point_offsets.get(i, 0) + self._core_offset
            if offset != 0:
                offsets[str(i)] = offset * 1000
        mem_off = self.mem_offset_spin.value()
        # EXPERIMENT: no longer duplicating mem_off into curve points 131/132
        # (NvAPI) alongside mem_offset_mhz (NVML) below — see _apply_gpu_offsets.
        # Same 2x NVML compensation as _apply_gpu_offsets (see there) — the
        # box holds the effective MHz, mem_offset_mhz stores the raw doubled
        # value that any apply path sends straight to NVML.
        mem_off_nvml = mem_off * 2
        vram_lock_max = self.vram_lock_max_spin.value()
        vram_lock_min = self.vram_lock_min_spin.value()
        if vram_lock_max != 0 and vram_lock_min == 0:
            vram_lock_min = vram_lock_max
        return {
            "name": "custom",
            "gpu_name": "NVIDIA GeForce RTX 4080 Laptop GPU",
            "curve_deltas": offsets,
            "mem_offset_mhz": mem_off_nvml,
            "power_limit_w": None,
            "mem_locked_min_mhz": vram_lock_min if vram_lock_max != 0 else None,
            "mem_locked_max_mhz": vram_lock_max if vram_lock_max != 0 else None,
        }

    # ─── GPU DEFAULT PROFILE (auto-applied by the tray at boot) ────────
    # Uses nvcurve's own `profile default` / `autoload` subcommands (see
    # root_helper.py op_set_default_gpu_profile / op_run_gpu_autoload).
    # Does NOT run a separate nvcurve daemon or systemd service — the
    # tray just triggers a single one-shot `nvcurve autoload` call at
    # startup. The current default is shown as a ★ next to its name in
    # the profile combo box rather than a separate label.
    def _read_default_gpu_profile_name(self):
        try:
            with open("/etc/nvcurve/config.json", "r") as f:
                cfg = json.load(f)
            profiles = cfg.get("auto_load_profiles", {})
            if not profiles and cfg.get("auto_load_profile"):
                profiles = {"idx:0": cfg["auto_load_profile"]}
            if profiles:
                return next(iter(profiles.values()))
        except (FileNotFoundError, json.JSONDecodeError):
            pass
        except Exception as e:
            self._log(f"⚠️ Could not read default GPU profile: {e}")
        return None

    def _refresh_default_star(self):
        """Lightweight update: re-marks the ★ on the current default
        item without rebuilding the whole combo or losing the selection."""
        default_name = self._read_default_gpu_profile_name()
        self.profile_combo.blockSignals(True)
        current_idx = self.profile_combo.currentIndex()
        for i in range(self.profile_combo.count()):
            plain = self._strip_default_mark(self.profile_combo.itemText(i))
            label = f"{plain}{self._DEFAULT_MARK}" if plain == default_name else plain
            if self.profile_combo.itemText(i) != label:
                self.profile_combo.setItemText(i, label)
        self.profile_combo.setCurrentIndex(current_idx)
        self.profile_combo.blockSignals(False)
        # Defensive: force an immediate repaint in case Qt doesn't pick up
        # the item-text change right away.
        self.profile_combo.update()
        # Diagnostic: always log what the backend actually persisted, so
        # it's visible in the log whether a "Default"/"Delete" click that
        # doesn't seem to change the ★ is a real backend failure (this line
        # will show the OLD name) or was already applied (shows the
        # expected new name).
        self._log(f"ℹ️ Default GPU profile is now: {default_name or '(none)'}")

    def _toggle_default_gpu_profile(self):
        """The 'Default' button is now a toggle: click once to mark the
        currently selected profile as default, click again on that same
        (already-starred) profile to unmark it."""
        profile_name = self._strip_default_mark(self.profile_combo.currentText())
        if not profile_name:
            self._log("❌ No profile selected.")
            return
        current_default = self._read_default_gpu_profile_name()
        if current_default == profile_name:
            self._clear_default_gpu_profile()
        else:
            self._set_default_gpu_profile()

    def _set_default_gpu_profile(self):
        profile_name = self._strip_default_mark(self.profile_combo.currentText())
        if not profile_name:
            self._log("❌ No profile selected to set as default.")
            return
        project_dir = os.path.dirname(os.path.abspath(__file__))
        payload = {
            "op": "set_default_gpu_profile",
            "project_dir": project_dir,
            "name": profile_name,
        }
        self._run_root_helper_command(
            payload,
            f"'{profile_name}' set as default GPU profile. "
            f"(RyzenAdj tray will auto-apply it on next startup.)",
            f"Could not set '{profile_name}' as default.",
            callback=lambda _: self._refresh_default_star(),
        )

    def _clear_default_gpu_profile(self):
        project_dir = os.path.dirname(os.path.abspath(__file__))
        payload = {
            "op": "set_default_gpu_profile",
            "project_dir": project_dir,
            "clear": True,
        }
        self._run_root_helper_command(
            payload,
            "Default GPU profile removed.",
            "Could not remove default GPU profile.",
            callback=lambda _: self._refresh_default_star(),
        )

    def _delete_gpu_profile(self):
        profile_name = self._strip_default_mark(self.profile_combo.currentText())
        if not profile_name:
            self._log("❌ No profile selected to delete.")
            return
        reply = QMessageBox.question(
            self,
            "Delete Profile",
            f"Delete profile '{profile_name}'? This cannot be undone.",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if reply != QMessageBox.Yes:
            return
        project_dir = os.path.dirname(os.path.abspath(__file__))
        payload = {
            "op": "delete_nvcurve_profile",
            "project_dir": project_dir,
            "name": profile_name,
        }
        self._run_root_helper_command(
            payload,
            f"Profile '{profile_name}' deleted.",
            f"Could not delete '{profile_name}'.",
            callback=lambda _: self._refresh_profile_list(),
        )

    # ─── GPU INFORMATION ──────────────────────────────────────────────
    def _update_gpu_info(self):
        if hasattr(self, 'nvml_available') and self.nvml_available and hasattr(self, 'nvml_handle'):
            try:
                nvml = self._nvml  # B9: cached; no import overhead
                temp = nvml.nvmlDeviceGetTemperature(self.nvml_handle, nvml.NVML_TEMPERATURE_GPU)
                self.gpu_temp.setText(f"Temp: {temp} °C")
                try:
                    mem_temp = nvml.nvmlDeviceGetTemperature(self.nvml_handle, nvml.NVML_TEMPERATURE_MEM)
                    self.gpu_mem_temp.setText(f"Mem Temp: {mem_temp} °C")
                except Exception:
                    self.gpu_mem_temp.setText("Mem Temp: N/A")
                try:
                    hotspot_temp = nvml.nvmlDeviceGetTemperature(self.nvml_handle, nvml.NVML_TEMPERATURE_HOTSPOT)
                    self.gpu_hotspot_temp.setText(f"Hotspot: {hotspot_temp} °C")
                except Exception:
                    self.gpu_hotspot_temp.setText("Hotspot: N/A")
                power = nvml.nvmlDeviceGetPowerUsage(self.nvml_handle) / 1000.0
                self.gpu_power.setText(f"Power: {power:.1f} W")
                clock_graphics = nvml.nvmlDeviceGetClockInfo(self.nvml_handle, nvml.NVML_CLOCK_GRAPHICS)
                clock_mem = nvml.nvmlDeviceGetClockInfo(self.nvml_handle, nvml.NVML_CLOCK_MEM)
                self.gpu_clock.setText(f"Clock: {clock_graphics} MHz")
                self.gpu_mem_clock.setText(f"Mem Clock: {clock_mem} MHz")
            except Exception:
                self._fallback_gpu_info()
        else:
            self._fallback_gpu_info()

    def _fallback_gpu_info(self):
        try:
            result = subprocess.run(
                ["nvidia-smi", "--query-gpu=temperature.gpu,power.draw,clocks.current.graphics,clocks.current.memory",
                 "--format=csv,noheader,nounits"],
                capture_output=True, text=True, timeout=2
            )
            if result.stdout:
                parts = [p.strip() for p in result.stdout.strip().split(',')]
                if len(parts) >= 4:
                    temp, power, clock, mem = parts
                    self.gpu_temp.setText(f"Temp: {temp} °C")
                    self.gpu_power.setText(f"Power: {power} W")
                    self.gpu_clock.setText(f"Clock: {clock} MHz")
                    self.gpu_mem_clock.setText(f"Mem Clock: {mem} MHz")
                    self.gpu_mem_temp.setText("Mem Temp: N/A")
                    self.gpu_hotspot_temp.setText("Hotspot: N/A")
        except Exception:
            pass

    def _on_curve_read_finished(self, output):
        self.btn_read_curve.setEnabled(True)
        self.btn_read_curve.setText("📥 Read Current Curve")
        if output is None:
            self._log("❌ Read operation failed.")
            return
        try:
            json_path = os.path.join(tempfile.gettempdir(), 'nvcurve_read.json')
            if not os.path.exists(json_path):
                self._log("❌ Curve data file not found.")
                return
            with open(json_path) as f:
                data = json.load(f)
            if "vf_curve" in data:
                all_points = data["vf_curve"]
                # Only take GPU-domain points for the graph
                gpu_points = [p for p in all_points if p.get("domain") == "gpu"]
                if gpu_points:
                    points = []
                    base_freqs = []
                    offsets = {}
                    # NOTE: this used to also read a "memory offset" back from
                    # NvAPI curve points 131/132 (freq_offset_kHz) and use it
                    # to overwrite mem_offset_spin. Since mem_offset_mhz is now
                    # applied via NVML (see profiles/apply.py), those NvAPI
                    # points no longer receive that write — reading them back
                    # here was showing a stale/disconnected value (the
                    # reported "value changes after Apply" symptom). There's
                    # no NVML-based mem-offset field in this JSON to read
                    # instead, so we simply leave mem_offset_spin as the user
                    # set it rather than overwrite it with wrong data.

                    # Process the GPU points
                    for p in gpu_points:
                        idx = p["index"]
                        current_freq_mhz = p["freq_kHz"] // 1000
                        volt_mv = p["volt_uV"] // 1000
                        offset_mhz = p.get("freq_offset_kHz", 0) // 1000
                        base_freq_mhz = current_freq_mhz - offset_mhz
                        points.append((volt_mv, base_freq_mhz))
                        base_freqs.append(base_freq_mhz)
                        offsets[idx] = offset_mhz

                    offset_values = list(offsets.values())
                    if offset_values and all(o == offset_values[0] for o in offset_values):
                        global_offset = offset_values[0]
                        self._core_offset = global_offset
                        self._point_offsets = {}
                        self.core_offset_spin.blockSignals(True)
                        self.core_offset_spin.setValue(global_offset)
                        self.core_offset_spin.blockSignals(False)
                        self._log(f"ℹ️ Global core offset detected: {global_offset} MHz")
                    else:
                        self._core_offset = 0
                        self.core_offset_spin.blockSignals(True)
                        self.core_offset_spin.setValue(0)
                        self.core_offset_spin.blockSignals(False)
                        self._point_offsets = offsets

                    self._curve_modified = False
                    self._default_points = points
                    self._default_base_freqs = base_freqs
                    self._gpu_indices = [p["index"] for p in gpu_points]
                    self._read_offsets = self._point_offsets.copy() if self._point_offsets else {}

                    self._flatten_threshold = -1
                    self.limit_spin.blockSignals(True)
                    self.limit_spin.setValue(-1)
                    self.limit_spin.blockSignals(False)

                    self.vf_widget.clear_selection()
                    self.vf_widget.set_points(points, base_freqs)
                    self._recompute_display()
                    self._log(f"✅ V/F curve loaded ({len(points)} GPU points).")
                    if points:
                        self.vf_widget.axis_y.setRange(0, max(f for _, f in points) + 200)
                        self.vf_widget.axis_x.setRange(min(v for v, _ in points) - 50, max(v for v, _ in points) + 50)
                else:
                    self._log("❌ No GPU points found in vf_curve.")
            else:
                self._log("❌ 'vf_curve' key not found in JSON response.")
        except Exception as e:
            self._log(f"❌ Error processing curve: {e}")
        finally:
            self._reset_graph_to_last_read()
        try:
            os.remove(json_path)
        except Exception:
            pass

    def _after_apply_read(self, output):
        if output is None:
            self._log("❌ Apply operation failed.")
            return
        try:
            json_path = os.path.join(tempfile.gettempdir(), 'nvcurve_apply_result.json')
            if not os.path.exists(json_path):
                self._log("❌ Apply result file not found.")
                return
            with open(json_path) as f:
                data = json.load(f)
            if "vf_curve" in data:
                all_points = data["vf_curve"]
                new_offsets = {}
                # NOTE: no longer reading a "memory offset" back from NvAPI
                # points 131/132 here — see the matching note in
                # _on_curve_read_finished above. mem_offset_spin is left as
                # the user set it.
                for p in all_points:
                    idx = p["index"]
                    offset_khz = p.get("freq_offset_kHz", 0)
                    if p.get("domain") == "gpu":
                        new_offsets[idx] = offset_khz // 1000
                self._point_offsets = new_offsets
                self._read_offsets = new_offsets.copy()
                self._core_offset = 0
                self.core_offset_spin.blockSignals(True)
                self.core_offset_spin.setValue(0)
                self.core_offset_spin.blockSignals(False)
                self._flatten_threshold = -1
                self.limit_spin.blockSignals(True)
                self.limit_spin.setValue(-1)
                self.limit_spin.blockSignals(False)
                self.vf_widget.clear_selection()
                self._recompute_display()
                self._log("✅ Offsets applied and curve updated.")
            else:
                self._log("❌ 'vf_curve' key not found in apply result.")
        except Exception as e:
            self._log(f"❌ Error processing apply result: {e}")
        finally:
            self._reset_graph_to_last_read()

    def _after_reset_read(self, output):
        self.btn_reset_curve.setEnabled(True)
        self.btn_reset_curve.setText("↺ Reset Curve")
        if output is None:
            self._log("❌ Reset operation failed.")
            return
        try:
            json_path = os.path.join(tempfile.gettempdir(), 'nvcurve_reset_result.json')
            if not os.path.exists(json_path):
                self._log("❌ Reset result file not found.")
                return
            with open(json_path) as f:
                data = json.load(f)
            if "vf_curve" in data:
                all_points = data["vf_curve"]
                gpu_points = [p for p in all_points if p.get("domain") == "gpu"]
                if gpu_points:
                    points = []
                    base_freqs = []
                    offsets = {}
                    # NOTE: "Reset Curve" zeros the NvAPI VF curve (points
                    # 0-132), but does NOT touch the separate NVML
                    # mem_offset_mhz — those are two different mechanisms
                    # (see profiles/apply.py). Showing the Memory Offset box
                    # as 0 here matches "everything reset" intent, but if the
                    # NVML offset is still actually applied on hardware, this
                    # display won't reflect that; there's no current backend
                    # path that also clears the NVML offset on curve reset.
                    mem_offset = 0

                    # Process the GPU points
                    for p in gpu_points:
                        idx = p["index"]
                        current_freq_mhz = p["freq_kHz"] // 1000
                        volt_mv = p["volt_uV"] // 1000
                        offset_mhz = p.get("freq_offset_kHz", 0) // 1000
                        base_freq_mhz = current_freq_mhz - offset_mhz
                        points.append((volt_mv, base_freq_mhz))
                        base_freqs.append(base_freq_mhz)
                        offsets[idx] = offset_mhz

                    self._default_points = points
                    self._default_base_freqs = base_freqs
                    self._gpu_indices = [p["index"] for p in gpu_points]
                    self._point_offsets = offsets
                    self._read_offsets = offsets.copy()
                    self._core_offset = 0
                    self._flatten_threshold = -1
                    self.mem_offset_spin.blockSignals(True)
                    self.mem_offset_spin.setValue(mem_offset)
                    self.mem_offset_spin.blockSignals(False)
                    self.core_offset_spin.blockSignals(True)
                    self.core_offset_spin.setValue(0)
                    self.core_offset_spin.blockSignals(False)
                    self.vf_widget.set_points(points, base_freqs)
                    self._recompute_display()
                    self._log(f"✅ V/F curve reset ({len(points)} GPU points).")
                    if points:
                        self.vf_widget.axis_y.setRange(0, max(f for _, f in points) + 200)
                        self.vf_widget.axis_x.setRange(min(v for v, _ in points) - 50, max(v for v, _ in points) + 50)
                else:
                    self._log("❌ No GPU points found in vf_curve.")
            else:
                self._log("❌ 'vf_curve' key not found in JSON response.")
        except Exception as e:
            self._log(f"❌ Error processing reset result: {e}")
        finally:
            self._reset_graph_to_last_read()


    # ═══════════════════════════════════════════════════════════════════════
    # TAB: RGB CONTROLS
    # ───────────────────────────────────────────────────────────────────────
    # alienfx_cli v1.1.0 wrapper. Does not require root — user must be in
    # the 'plugdev' group for USB HID access.
    #
    # Supported commands:
    #   setone   <dev> <idx> <r> <g> <b>
    #   setglobal <dev> <type> <mode> <r> <g> <b>
    #   setdim   <dev> <brightness>
    #   setaction <dev> <idx> <action> <r> <g> <b> [<action> <r> <g> <b> ...]
    #
    # Commands run non-blocking via QProcess; output streams to Terminal Logs.
    # ═══════════════════════════════════════════════════════════════════════
    # ═══════════════════════════════════════════════════════════════════════
    # TAB: RGB CONTROLS
    # ───────────────────────────────────────────────────────────────────────
    # ═══════════════════════════════════════════════════════════════════════
    # TAB: RGB CONTROLS
    # ───────────────────────────────────────────────────────────────────────
    def _build_rgb_manual_page(self) -> QWidget:
        """Original manual/effects RGB controls page (returned as widget)."""
        tab = QWidget()
        tab.setStyleSheet(f"background:{C_BG};")

        # MAIN LAYOUT: top 3 columns + bottom profile management
        outer_layout = QVBoxLayout(tab)
        outer_layout.setSpacing(6)
        outer_layout.setContentsMargins(8, 8, 8, 8)

        main_layout = QHBoxLayout()
        main_layout.setSpacing(10)

        # ── Helper Functions ──────────────────────────────────────────────
        def _sp(lo, hi, val, w=54, tip=""):
            s = QSpinBox()
            s.setRange(lo, hi)
            s.setValue(val)
            s.setFixedWidth(w)
            s.setFont(get_font(8))
            s.setFocusPolicy(Qt.StrongFocus)
            s.setKeyboardTracking(True)
            s.setAlignment(Qt.AlignRight)
            if tip:
                s.setToolTip(tip)
            return s

        def _yl(txt, size=8):
            return SL(txt, color=C_YELLOW, size=size)

        def _gl(txt, size=8):
            return SL(txt, color=C_DGREY, size=size)

        # ──────────────────────────────────────────────────────────────────
        # COLUMN 1: Device + Single Light + Global Brightness
        # ──────────────────────────────────────────────────────────────────
        col1 = QVBoxLayout()
        col1.setSpacing(8)

        # [1] Device Group
        g_dev = QGroupBox(" DEVICE ")
        vd = QVBoxLayout(g_dev)
        vd.setSpacing(4)
        vd.setContentsMargins(8, 8, 8, 8)
        dr = QHBoxLayout()
        dr.addWidget(_yl("Dev:"))

        max_dev = max(0, self._rgb_dev_count - 1) if self._rgb_dev_count > 0 else 0
        self._rgb_device = _sp(0, max_dev, 0, 44, "Device index")
        dr.addWidget(self._rgb_device)

        dr.addStretch()
        vd.addLayout(dr)

        hint_txt = f"{self._rgb_dev_count} device(s) found" if self._rgb_dev_count > 0 else ("alienfx_cli not found" if not self._alienfx_cli else "no devices detected")
        self._rgb_dev_hint = _gl(hint_txt, 7)
        vd.addWidget(self._rgb_dev_hint)
        self._rgb_dev_raw = _gl("", 6)
        vd.addWidget(self._rgb_dev_raw)
        col1.addWidget(g_dev)

        # [2] Single Light Group — multi-light support via range selection
        g_sl = QGroupBox(" SINGLE LIGHT CONTROL ")
        vsl = QVBoxLayout(g_sl)
        vsl.setSpacing(6)
        vsl.setContentsMargins(8, 8, 8, 8)

        r_from = QHBoxLayout()
        r_from.addWidget(_yl("From idx:"))
        r_from.addStretch()
        self._rgb_one_from = _sp(0, 255, 0, 44)  # range extended (lightids up to 135)
        r_from.addWidget(self._rgb_one_from)
        vsl.addLayout(r_from)

        r_to = QHBoxLayout()
        r_to.addWidget(_yl("To idx:"))
        r_to.addStretch()
        self._rgb_one_to = _sp(0, 255, 0, 44)    # range extended
        r_to.addWidget(self._rgb_one_to)
        vsl.addLayout(r_to)

        # When From changes, make To at least as large as From
        def _sync_range():
            if self._rgb_one_to.value() < self._rgb_one_from.value():
                self._rgb_one_to.setValue(self._rgb_one_from.value())
        self._rgb_one_from.valueChanged.connect(_sync_range)

        vsl.addStretch()

        btn1 = QPushButton("💡 Apply Light(s)")
        btn1.setObjectName("apply_button")
        btn1.setFixedHeight(26)
        def _apply_range():
            lo = self._rgb_one_from.value()
            hi = self._rgb_one_to.value()
            dev = str(self._rgb_device.value())
            r, g, b = str(self._rgb_r.value()), str(self._rgb_g.value()), str(self._rgb_b.value())
            for idx in range(lo, hi + 1):
                self._rgb_run(["setone", dev, str(idx), r, g, b])
        btn1.clicked.connect(_apply_range)
        vsl.addWidget(btn1)
        col1.addWidget(g_sl)

        # [3] Global Brightness Group (New Slider Design)
        g_br = QGroupBox(" GLOBAL BRIGHTNESS ")
        vbr = QVBoxLayout(g_br)
        vbr.setSpacing(6)
        vbr.setContentsMargins(8, 8, 8, 8)

        r_brow = QHBoxLayout()
        r_brow.addWidget(_yl("Brightness:"))
        self._rgb_brightness = _sp(0, 255, 255, 44)
        r_brow.addWidget(self._rgb_brightness)
        vbr.addLayout(r_brow)

        # Brightness slider added at the bottom, as in the mockup
        self._slider_brightness = QSlider(Qt.Horizontal)
        self._slider_brightness.setRange(0, 255)
        self._slider_brightness.setValue(255)
        self._slider_brightness.setStyleSheet(
            f"QSlider::groove:horizontal {{ background:{C_BG3}; height:4px; border-radius:2px; }}"
            f"QSlider::handle:horizontal {{ background:{C_CYAN}; width:12px; margin:-4px 0; border-radius:6px; }}"
        )
        # Link the slider and the number box together (buddy effect)
        self._slider_brightness.valueChanged.connect(self._rgb_brightness.setValue)
        self._rgb_brightness.valueChanged.connect(self._slider_brightness.setValue)
        vbr.addWidget(self._slider_brightness)
        vbr.addStretch()

        btn2 = QPushButton("💡 Apply Brightness")
        btn2.setObjectName("apply_button")
        btn2.setFixedHeight(26)
        btn2.clicked.connect(lambda: self._rgb_run(["setdim", str(self._rgb_device.value()), str(self._rgb_brightness.value())]))
        vbr.addWidget(btn2)
        col1.addWidget(g_br)

        main_layout.addLayout(col1, stretch=1)

        # ──────────────────────────────────────────────────────────────────
        # COLUMN 2: Global Color + Global Effects
        # ──────────────────────────────────────────────────────────────────
        col2 = QVBoxLayout()
        col2.setSpacing(8)

        # [1] Global Color Group
        g_col = QGroupBox(" GLOBAL COLOR ")
        vcol_main = QVBoxLayout(g_col)
        vcol_main.setSpacing(6)
        vcol_main.setContentsMargins(8, 8, 8, 8)

        rgb_grid = QGridLayout()
        rgb_grid.setSpacing(4)
        rgb_grid.setColumnStretch(2, 1)

        def _rgb_col_grid(grid, row_idx, lbl, clr, dflt):
            l = SL(lbl, bold=True, color=clr, size=8)
            l.setAlignment(Qt.AlignCenter)
            sp = QSpinBox()
            sp.setRange(0, 255)
            sp.setValue(dflt)
            sp.setFixedWidth(56)
            sl = QSlider(Qt.Horizontal)
            sl.setRange(0, 255)
            sl.setValue(dflt)
            sl.setStyleSheet(
                f"QSlider::groove:horizontal {{ background:{C_BG3}; height:4px; border-radius:2px; }}"
                f"QSlider::handle:horizontal {{ background:{clr}; width:12px; margin:-4px 0; border-radius:6px; }}"
            )
            sp.valueChanged.connect(sl.setValue)
            sl.valueChanged.connect(sp.setValue)
            grid.addWidget(l, row_idx, 0)
            grid.addWidget(sp, row_idx, 1)
            grid.addWidget(sl, row_idx, 2)
            return sp, sl

        self._rgb_r, _br = _rgb_col_grid(rgb_grid, 0, "R", C_STOP, 255)
        self._rgb_g, _bg = _rgb_col_grid(rgb_grid, 1, "G", C_GREEN, 0)
        self._rgb_b, _bb = _rgb_col_grid(rgb_grid, 2, "B", C_BLUE, 0)
        vcol_main.addLayout(rgb_grid)

        bottom_color_row = QHBoxLayout()
        self._rgb_preview = QFrame()
        self._rgb_preview.setFixedSize(36, 36)
        self._rgb_preview.setStyleSheet("background:#ff0000;border:1px solid #444;border-radius:4px;")
        bottom_color_row.addWidget(self._rgb_preview)

        def _rgb_chg():
            r, g, b = self._rgb_r.value(), self._rgb_g.value(), self._rgb_b.value()
            _br.setValue(r)
            _bg.setValue(g)
            _bb.setValue(b)
            self._rgb_preview.setStyleSheet(f"background:#{r:02x}{g:02x}{b:02x};border:1px solid #444;border-radius:4px;")
            self._rgb_last_color = (r, g, b)

        self._rgb_r.valueChanged.connect(_rgb_chg)
        self._rgb_g.valueChanged.connect(_rgb_chg)
        self._rgb_b.valueChanged.connect(_rgb_chg)
        _rgb_chg()

        # Quick Color Palette Section
        QC = [("#ff0000","R",255,0,0), ("#00ff00","G",0,255,0), ("#0000ff","B",0,0,255),
              ("#00ffe0","C",0,255,224), ("#fe8019","O",254,128,25), ("#ffffff","W",255,255,255),
              ("#800080","P",128,0,128), ("#000000","—",0,0,0)]
        qv = QVBoxLayout()
        qv.setSpacing(3)
        for row_qc in (QC[:4], QC[4:]):
            qr = QHBoxLayout()
            qr.setSpacing(3)
            for hx, lb, rv, gv2, bv in row_qc:
                b = QPushButton(lb)
                b.setFixedSize(22, 18)
                b.setFont(get_font(7, True))
                lum = rv + gv2 + bv
                b.setStyleSheet(
                    f"QPushButton{{background:{hx};color:{'#000' if lum > 380 else '#eee'};"
                    f"border:1px solid #333;border-radius:2px;padding:0;}}"
                    f"QPushButton:hover{{border:2px solid {C_CYAN};}}")
                b.clicked.connect(lambda _, r=rv, g=gv2, bl=bv: [self._rgb_r.setValue(r), self._rgb_g.setValue(g), self._rgb_b.setValue(bl)])
                qr.addWidget(b)
            qr.addStretch()
            qv.addLayout(qr)
        bottom_color_row.addLayout(qv)
        bottom_color_row.addStretch()
        vcol_main.addLayout(bottom_color_row)
        col2.addWidget(g_col)

        # [2] Global Effects Group
        g_gl = QGroupBox(" GLOBAL EFFECTS ")
        vgl = QVBoxLayout(g_gl)
        vgl.setSpacing(6)
        vgl.setContentsMargins(8, 8, 8, 8)

        KT = {2: "Breathe/Pulse", 7: "Spectrum Cycle", 15: "Wave", 16: "Rainbow Wave"}
        r_gt = QHBoxLayout()
        r_gt.addWidget(_yl("Type:"))
        self._gt_hint = SL("", color=C_CYAN, size=7)
        r_gt.addWidget(self._gt_hint)
        r_gt.addStretch()
        self._rgb_glob_type = _sp(0, 30, 1, 44)
        r_gt.addWidget(self._rgb_glob_type)
        vgl.addLayout(r_gt)

        r_gm = QHBoxLayout()
        r_gm.addWidget(_yl("Mode:"))
        r_gm.addStretch()
        self._rgb_glob_mode = _sp(0, 30, 1, 44)
        r_gm.addWidget(self._rgb_glob_mode)
        vgl.addLayout(r_gm)

        def _updg(*_):
            self._gt_hint.setText(KT.get(self._rgb_glob_type.value(), ""))

        vgl.addStretch()
        btn3 = QPushButton("🌈 Apply Global Effect")
        btn3.setObjectName("run_button")
        btn3.setFixedHeight(26)
        def _do3():
            self._rgb_run(["--tempo", str(self._rgb_tempo.value()), "--length", str(self._rgb_length.value()),
                           "setglobal", str(self._rgb_device.value()), str(self._rgb_glob_type.value()),
                           str(self._rgb_glob_mode.value()), str(self._rgb_r.value()), str(self._rgb_g.value()), str(self._rgb_b.value())])
        btn3.clicked.connect(_do3)
        vgl.addWidget(btn3)
        col2.addWidget(g_gl)

        main_layout.addLayout(col2, stretch=1)

        # ──────────────────────────────────────────────────────────────────
        # COLUMN 3: Animated Lighting + Animation Parameters
        # ──────────────────────────────────────────────────────────────────
        col3 = QVBoxLayout()
        col3.setSpacing(8)

        # [1] Animated Lighting Group — only applies to device 0
        g_act = QGroupBox(" ANIMATED LIGHTING (Device 0 — Keyboard) ")
        vact = QVBoxLayout(g_act)
        vact.setSpacing(6)
        vact.setContentsMargins(8, 8, 8, 8)

        r_ai = QHBoxLayout()
        r_ai.addWidget(_yl("Light idx:"))
        r_ai.addStretch()
        self._rgb_act_idx = _sp(0, 63, 0, 44)
        r_ai.addWidget(self._rgb_act_idx)
        r_ai.addStretch()
        vact.addLayout(r_ai)

        # Table Column Headers
        hdr = QHBoxLayout()
        hdr.setSpacing(3)
        lbl_act = _gl("Action", 7)
        lbl_act.setFixedWidth(78)
        lbl_r = _gl("R", 7)
        lbl_r.setFixedWidth(48)
        lbl_r.setAlignment(Qt.AlignCenter)
        lbl_g = _gl("G", 7)
        lbl_g.setFixedWidth(48)
        lbl_g.setAlignment(Qt.AlignCenter)
        lbl_b = _gl("B", 7)
        lbl_b.setFixedWidth(48)
        lbl_b.setAlignment(Qt.AlignCenter)
        hdr.addWidget(lbl_act)
        hdr.addWidget(lbl_r)
        hdr.addWidget(lbl_g)
        hdr.addWidget(lbl_b)
        hdr.addStretch()
        vact.addLayout(hdr)

        # Dynamic Table Row Area
        self._action_rows_widget = QWidget()
        self._action_rows_widget.setStyleSheet(f"background:{C_BG};")
        self._action_rows_layout = QVBoxLayout(self._action_rows_widget)
        self._action_rows_layout.setSpacing(3)
        self._action_rows_layout.setContentsMargins(0, 0, 0, 0)
        self._action_rows_layout.setAlignment(Qt.AlignTop)
        self._action_row_data = []
        ACTIONS = ["color", "pulse", "morph", "rainbow", "breath"]

        CS = (f"QComboBox{{background:{C_BG3};color:{C_YELLOW};border:1px solid {C_BORDER};border-radius:2px;padding:1px 2px;font-size:7pt;}}"
              f"QComboBox::drop-down{{border:none;width:12px;}}")

        def _ss(col):
            return f"QSpinBox{{background:{C_BG3};color:{col};border:1px solid {C_BORDER};border-radius:2px;font-size:7pt;}}"

        def _add_row(act="color", rv=255, gv=0, bv=0):
            rw = QWidget()
            rh = QHBoxLayout(rw)
            rh.setSpacing(3)
            rh.setContentsMargins(0, 0, 0, 0)

            cb = QComboBox()
            cb.addItems(ACTIONS)
            cb.setCurrentText(act)
            cb.setFixedWidth(78)
            cb.setStyleSheet(CS)

            rs = _sp(0, 255, rv, 48)
            rs.setStyleSheet(_ss(C_STOP))
            gs = _sp(0, 255, gv, 48)
            gs.setStyleSheet(_ss(C_GREEN))
            bs = _sp(0, 255, bv, 48)
            bs.setStyleSheet(_ss(C_BLUE))

            _qstyle = QApplication.style()
            _copy_icon = QIcon.fromTheme("edit-copy")
            if _copy_icon.isNull():
                _copy_icon = _qstyle.standardIcon(QStyle.SP_FileDialogContentsView)
            bsync = QPushButton()
            bsync.setFixedSize(22, 20)
            bsync.setIcon(_copy_icon)
            bsync.setIconSize(QSize(14, 14))
            bsync.setToolTip("Copy R/G/B from main selector")
            bsync.setStyleSheet(f"QPushButton{{background:{C_BG3};border:1px solid {C_BORDER};}}")
            bsync.clicked.connect(lambda: [rs.setValue(self._rgb_r.value()), gs.setValue(self._rgb_g.value()), bs.setValue(self._rgb_b.value())])

            _del_icon = QIcon.fromTheme("list-remove")
            if _del_icon.isNull():
                _del_icon = _qstyle.standardIcon(QStyle.SP_TitleBarCloseButton)
            bdel = QPushButton()
            bdel.setFixedSize(20, 20)
            bdel.setIcon(_del_icon)
            bdel.setIconSize(QSize(14, 14))
            bdel.setToolTip("Remove row")
            bdel.setStyleSheet(f"QPushButton{{background:{C_BG3};border:1px solid {C_BORDER};}}")

            entry = (cb, rs, gs, bs, rw)
            bdel.clicked.connect(lambda: [self._action_row_data.remove(entry), rw.setParent(None), rw.deleteLater(), _upda()])

            rh.addWidget(cb)
            rh.addWidget(rs)
            rh.addWidget(gs)
            rh.addWidget(bs)
            rh.addWidget(bsync)
            rh.addWidget(bdel)

            self._action_row_data.append(entry)
            self._action_rows_layout.addWidget(rw)
            _upda()

        def _upda(*_):
            pass

        _add_row("rainbow", 255, 0, 0)
        _add_row("rainbow", 0, 0, 255)

        sa = QScrollArea()
        sa.setWidget(self._action_rows_widget)
        sa.setWidgetResizable(True)
        sa.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        sa.setStyleSheet(f"QScrollArea{{background:{C_BG};border:1px solid {C_BORDER};}}")
        vact.addWidget(sa, stretch=1)

        r_add_apply = QHBoxLayout()
        btnadd = QPushButton("＋ Add row")
        btnadd.setFixedHeight(24)
        btnadd.setStyleSheet(f"QPushButton{{background:{C_BG2};color:{C_CYAN};border:1px solid {C_CYAN};}}")
        btnadd.clicked.connect(lambda: _add_row())

        btnapply = QPushButton("✨ Apply Animation")
        btnapply.setObjectName("apply_button")
        btnapply.setFixedHeight(24)
        def _do4():
            args = ["--tempo", str(self._rgb_tempo.value()), "--length", str(self._rgb_length.value()), "setaction", "0", str(self._rgb_act_idx.value())]
            for cb, rs, gs, bs, _ in self._action_row_data:
                args += [cb.currentText(), str(rs.value()), str(gs.value()), str(bs.value())]
            self._rgb_run(args)
        btnapply.clicked.connect(_do4)

        r_add_apply.addWidget(btnadd)
        r_add_apply.addWidget(btnapply, stretch=1)
        vact.addLayout(r_add_apply)
        col3.addWidget(g_act, stretch=2)

        # [2] Animation Parameters Group (New Dual-Slider Design)
        g_ap = QGroupBox(" ANIMATION PARAMETERS ")
        vap = QVBoxLayout(g_ap)
        vap.setSpacing(6)
        vap.setContentsMargins(8, 8, 8, 8)

        # Tempo (Speed) Row and Slider
        r_t = QHBoxLayout()
        r_t.addWidget(_yl("Tempo:"))
        self._rgb_tempo = _sp(1, 50, 5, 44)
        r_t.addWidget(self._rgb_tempo)
        vap.addLayout(r_t)

        self._slider_tempo = QSlider(Qt.Horizontal)
        self._slider_tempo.setRange(1, 50)
        self._slider_tempo.setValue(5)
        self._slider_tempo.setStyleSheet(
            f"QSlider::groove:horizontal {{ background:{C_BG3}; height:4px; border-radius:2px; }}"
            f"QSlider::handle:horizontal {{ background:{C_CYAN}; width:12px; margin:-4px 0; border-radius:6px; }}"
        )
        self._slider_tempo.valueChanged.connect(self._rgb_tempo.setValue)
        self._rgb_tempo.valueChanged.connect(self._slider_tempo.setValue)
        vap.addWidget(self._slider_tempo)

        # Length (Wavelength) Row and Slider
        r_l = QHBoxLayout()
        r_l.addWidget(_yl("Length:"))
        self._rgb_length = _sp(1, 20, 5, 44)
        r_l.addWidget(self._rgb_length)
        vap.addLayout(r_l)

        self._slider_length = QSlider(Qt.Horizontal)
        self._slider_length.setRange(1, 20)
        self._slider_length.setValue(5)
        self._slider_length.setStyleSheet(
            f"QSlider::groove:horizontal {{ background:{C_BG3}; height:4px; border-radius:2px; }}"
            f"QSlider::handle:horizontal {{ background:{C_CYAN}; width:12px; margin:-4px 0; border-radius:6px; }}"
        )
        self._slider_length.valueChanged.connect(self._rgb_length.setValue)
        self._rgb_length.valueChanged.connect(self._slider_length.setValue)
        vap.addWidget(self._slider_length)

        col3.addWidget(g_ap, stretch=1)

        # Add the 3 columns to the main layout and set the width ratios (stretch)
        main_layout.addLayout(col3, stretch=2)

        outer_layout.addLayout(main_layout, stretch=1)

        # ──────────────────────────────────────────────────────────────────
        # PROFILE BAR — profile management at the bottom
        # ──────────────────────────────────────────────────────────────────
        g_prof = QGroupBox(" RGB PROFILES ")
        hprof = QHBoxLayout(g_prof)
        hprof.setSpacing(6)
        hprof.setContentsMargins(8, 4, 8, 4)

        hprof.addWidget(_yl("Profile:"))
        self._rgb_profile_combo = QComboBox()
        self._rgb_profile_combo.setMinimumWidth(140)
        self._rgb_profile_combo.setStyleSheet(
            f"QComboBox{{background:{C_BG3};color:{C_CYAN};border:1px solid {C_BORDER};"
            f"border-radius:2px;padding:2px 6px;font-size:8pt;}}"
            f"QComboBox::drop-down{{border:none;width:14px;}}")
        hprof.addWidget(self._rgb_profile_combo, stretch=1)

        _pbtn_style = (f"QPushButton{{background:{C_BG2};color:{C_CYAN};border:1px solid {C_BORDER};"
                       f"border-radius:3px;padding:2px 8px;font-size:7pt;font-weight:bold;}}"
                       f"QPushButton:hover{{background:{C_CYAN};color:{C_BG2};}}")

        btn_load = QPushButton("📂 Load")
        btn_load.setStyleSheet(_pbtn_style)
        btn_load.setFixedHeight(24)
        btn_load.clicked.connect(self._rgb_load_profile)
        hprof.addWidget(btn_load)

        btn_save = QPushButton("💾 Save")
        btn_save.setStyleSheet(_pbtn_style)
        btn_save.setFixedHeight(24)
        btn_save.clicked.connect(self._rgb_save_profile)
        hprof.addWidget(btn_save)

        btn_def = QPushButton("⭐ Set Default")
        btn_def.setStyleSheet(_pbtn_style)
        btn_def.setFixedHeight(24)
        btn_def.clicked.connect(self._rgb_set_default)
        hprof.addWidget(btn_def)

        btn_del = QPushButton("🗑️ Delete")
        btn_del.setStyleSheet(
            f"QPushButton{{background:{C_BG2};color:{C_STOP};border:1px solid {C_BORDER};"
            f"border-radius:3px;padding:2px 8px;font-size:7pt;font-weight:bold;}}"
            f"QPushButton:hover{{background:{C_STOP};color:{C_BG2};}}")
        btn_del.setFixedHeight(24)
        btn_del.clicked.connect(self._rgb_delete_profile)
        hprof.addWidget(btn_del)

        btn_clr = QPushButton("🔄 Clear History")
        btn_clr.setStyleSheet(_pbtn_style)
        btn_clr.setFixedHeight(24)
        btn_clr.clicked.connect(self._rgb_clear_history)
        hprof.addWidget(btn_clr)

        btn_rst = QPushButton("🔴 Reset All Settings")
        btn_rst.setStyleSheet(
            f"QPushButton{{background:{C_BG2};color:{C_STOP};border:1px solid {C_STOP};"
            f"border-radius:3px;padding:2px 8px;font-size:7pt;font-weight:bold;}}"
            f"QPushButton:hover{{background:{C_STOP};color:{C_BG2};}}")
        btn_rst.setFixedHeight(24)
        btn_rst.clicked.connect(self._rgb_reset_all_settings)
        hprof.addWidget(btn_rst)

        outer_layout.addWidget(g_prof)
        self._rgb_refresh_profile_combo()

        # Signal Connections (change triggers)
        for s in (self._rgb_device.valueChanged, self._rgb_glob_type.valueChanged,
                  self._rgb_glob_mode.valueChanged, self._rgb_r.valueChanged,
                  self._rgb_g.valueChanged, self._rgb_b.valueChanged,
                  self._rgb_tempo.valueChanged, self._rgb_length.valueChanged):
            s.connect(_updg)
        _updg()

        return tab

    def _build_tab_rgb(self):
        """Create RGB Controls tab with inner sub-tabs: Manual/Effects and Per-Key."""
        outer = QWidget()
        outer.setStyleSheet(f"background:{C_BG};")
        outer_lay = QVBoxLayout(outer)
        outer_lay.setSpacing(0)
        outer_lay.setContentsMargins(0, 0, 0, 0)

        # Inner tab widget with smaller tab bar
        self._rgb_subtabs = QTabWidget()
        self._rgb_subtabs.setStyleSheet(
            f"QTabBar::tab {{ min-width: 60px; padding: 4px 14px; font-size: 7pt; }}"
        )
        outer_lay.addWidget(self._rgb_subtabs)

        # Page 1: existing manual controls
        manual_page = self._build_rgb_manual_page()
        self._rgb_subtabs.addTab(manual_page, "  ✦ MANUAL / EFFECTS  ")

        # Page 2: per-key RGB grid
        perkey_page = self._build_rgb_perkey_page()
        self._rgb_subtabs.addTab(perkey_page, "  ⌨ PER-KEY  ")

        self.tabs.addTab(outer, "  🌈 RGB CONTROLS  ")

    def _load_alienfx_mappings(self) -> dict | None:
        """Parse ~/.local/share/alienfx/mappings.json.

        Returns:
            {"vid": int, "pid": int, "name": str, "lights": {lightid: name}}
            or None on error / file not found.
        """
        mappings_path = Path.home() / ".local/share/alienfx/mappings.json"
        try:
            if not mappings_path.exists():
                self._log(f"[perkey] mappings.json not found: {mappings_path}")
                return None
            data = json.loads(mappings_path.read_text(encoding="utf-8"))
        except Exception as e:
            self._log(f"[perkey] mappings.json parse error: {e}")
            return None

        devices = data.get("devices", [])
        if not devices:
            self._log("[perkey] mappings.json: no devices found")
            return None

        # Pick device with the most lights (keyboard heuristic)
        best = max(devices, key=lambda d: len(d.get("lights", [])))
        lights_raw = best.get("lights", [])

        # Build lightid → name dict; group by name to merge duplicates (e.g. space lid 106+107)
        lights: dict[int, str] = {}
        for entry in lights_raw:
            lid = entry.get("lightid")
            name = entry.get("name", "")
            if lid is not None and name:
                lights[int(lid)] = name

        self._log(
            f"[perkey] loaded {len(lights)} lights from '{best.get('name', '?')}' "
            f"(vid={best.get('vid')}, pid={best.get('pid')})"
        )
        return {
            "vid": int(best.get("vid", 0)),
            "pid": int(best.get("pid", 0)),
            "name": best.get("name", ""),
            "lights": lights,
        }
    def _build_rgb_perkey_page(self) -> QWidget:
        """Build the Per-Key lighting control page."""
        page = QWidget()
        page.setStyleSheet(f"background:{C_BG};")
        vlay = QVBoxLayout(page)
        vlay.setSpacing(4)
        vlay.setContentsMargins(8, 8, 8, 8)

        # ── Load mappings ──────────────────────────────────────────────────────
        mappings = self._load_alienfx_mappings()
        self._perkey_mappings = mappings

        if mappings is None:
            # Error state: show warning + reload button
            err_frame = QWidget()
            err_lay = QVBoxLayout(err_frame)
            err_lay.setAlignment(Qt.AlignCenter)
            err_lbl = QLabel(
                "⚠  mappings.json not found at  ~/.local/share/alienfx/\n"
                "Run  alienfx-gui  (or  alienfx_cli probe)  once to generate it,\n"
                "then click Reload."
            )
            err_lbl.setAlignment(Qt.AlignCenter)
            err_lbl.setWordWrap(True)
            err_lbl.setStyleSheet(f"color:{C_YELLOW};font-size:9pt;")
            err_lay.addWidget(err_lbl)
            btn_reload = QPushButton("⟳  Reload mappings.json")
            btn_reload.setObjectName("apply_button")
            btn_reload.setFixedHeight(28)
            btn_reload.clicked.connect(self._perkey_reload)
            err_lay.addWidget(btn_reload, alignment=Qt.AlignHCenter)
            vlay.addStretch()
            vlay.addWidget(err_frame)
            vlay.addStretch()
            self._perkey_page = page
            return page

        # ── Build name → lightids mapping ────────────────────────────────────
        # Start: group lights by name (handles space with lid 106+107 sharing one name)
        name_to_lids: dict[str, list[int]] = {}
        for lid, name in mappings["lights"].items():
            if name not in name_to_lids:
                name_to_lids[name] = []
            name_to_lids[name].append(lid)

        # Apply PERKEY_ALIASES: merge secondary zones into primary key name
        for primary, alias_list in PERKEY_ALIASES.items():
            for alias in alias_list:
                if alias != primary and alias in name_to_lids:
                    if primary not in name_to_lids:
                        name_to_lids[primary] = []
                    name_to_lids[primary].extend(name_to_lids.pop(alias))

        self._perkey_name_to_lids = name_to_lids

        # Reset per-page button registry (rebuild on each reload)
        self._perkey_buttons = {}

        # ── Keyboard Grid GroupBox ───────────────────────────────────────────
        g_grid = QGroupBox(" KEYBOARD LAYOUT ")
        vgrid = QVBoxLayout(g_grid)
        vgrid.setContentsMargins(6, 8, 6, 6)
        vgrid.setSpacing(2)

        used_names: set[str] = set()  # track which layout names exist in mappings

        for row_keys in PERKEY_LAYOUT:
            row_lay = QHBoxLayout()
            row_lay.setSpacing(2)
            row_lay.setContentsMargins(0, 0, 0, 0)
            for key_name, span in row_keys:
                lids = list(name_to_lids.get(key_name, []))
                btn = KeyButton(key_name, lids)
                # Single click = toggle selection
                btn.clicked.connect(
                    lambda checked, b=btn: self._perkey_update_sel_count()
                )
                # Double click = immediately apply current color
                btn.set_double_click_action(
                    (lambda b=btn: self._perkey_double_click(b))
                )
                if not lids:
                    btn.setEnabled(False)
                    btn.setToolTip(f"{key_name} — not in mappings.json")
                else:
                    used_names.add(key_name)

                # Restore state if we have one (perkey_reload scenario)
                existing_color = self._perkey_key_state.get(key_name)
                if existing_color:
                    btn.set_color(existing_color)

                row_lay.addWidget(btn, stretch=span)
                self._perkey_buttons[key_name] = btn
            vgrid.addLayout(row_lay)

        # ── EXTRAS: keys in mappings but not in the layout grid ───────────────
        extras_names = set(name_to_lids.keys()) - used_names
        if extras_names:
            g_extras = QGroupBox(" EXTRAS (unmapped in grid) ")
            vex = QHBoxLayout(g_extras)
            vex.setContentsMargins(4, 4, 4, 4)
            vex.setSpacing(2)
            for ex_name in sorted(extras_names):
                lids = name_to_lids.get(ex_name, [])
                btn = KeyButton(ex_name, lids)
                btn.clicked.connect(
                    lambda checked, b=btn: self._perkey_update_sel_count()
                )
                btn.set_double_click_action((lambda b=btn: self._perkey_double_click(b)))
                existing_color = self._perkey_key_state.get(ex_name)
                if existing_color:
                    btn.set_color(existing_color)
                vex.addWidget(btn)
                self._perkey_buttons[ex_name] = btn
            vex.addStretch()
            vgrid.addWidget(g_extras)

        vlay.addWidget(g_grid, stretch=1)

        # ── SELECTION + DEVICE row ────────────────────────────────────────────
        g_sel = QGroupBox(" SELECTION ")
        hsel = QHBoxLayout(g_sel)
        hsel.setContentsMargins(8, 4, 8, 4)
        hsel.setSpacing(6)

        _sel_btn_style = (
            f"QPushButton{{background:{C_BG2};color:{C_CYAN};border:1px solid {C_BORDER};"
            f"border-radius:3px;padding:2px 10px;font-size:7pt;font-weight:bold;}}"
            f"QPushButton:hover{{background:{C_CYAN};color:{C_BG};}}"
        )

        for lbl, fn in [("All", lambda: self._perkey_select_all(True)),
                        ("None", lambda: self._perkey_select_all(False)),
                        ("Invert", self._perkey_invert_selection)]:
            b = QPushButton(lbl)
            b.setStyleSheet(_sel_btn_style)
            b.setFixedHeight(24)
            b.clicked.connect(fn)
            hsel.addWidget(b)

        self._perkey_sel_label = SL("0 selected", color=C_DGREY, size=7)
        hsel.addWidget(self._perkey_sel_label)
        hsel.addStretch()

        # Keyboard is always device #1
        hsel.addWidget(SL("Dev: #1 (keyboard)", color=C_DGREY, size=6))

        vlay.addWidget(g_sel)

        # ── COLOR row ────────────────────────────────────────────────────────
        g_color = QGroupBox(" COLOR ")
        hcol = QHBoxLayout(g_color)
        hcol.setContentsMargins(8, 4, 8, 4)
        hcol.setSpacing(6)

        def _pk_sp(val: int, color: str) -> QSpinBox:
            sp = QSpinBox()
            sp.setRange(0, 255)
            sp.setValue(val)
            sp.setFixedWidth(52)
            sp.setFont(get_font(8))
            sp.setAlignment(Qt.AlignRight)
            sp.setStyleSheet(
                f"QSpinBox{{background:{C_BG3};color:{color};"
                f"border:1px solid {C_BORDER};border-radius:2px;font-size:7pt;}}"
            )
            return sp

        hcol.addWidget(SL("R", bold=True, color=C_STOP, size=8))
        self._pk_r = _pk_sp(255, C_STOP)
        hcol.addWidget(self._pk_r)

        hcol.addWidget(SL("G", bold=True, color=C_GREEN, size=8))
        self._pk_g = _pk_sp(0, C_GREEN)
        hcol.addWidget(self._pk_g)

        hcol.addWidget(SL("B", bold=True, color=C_BLUE, size=8))
        self._pk_b = _pk_sp(0, C_BLUE)
        hcol.addWidget(self._pk_b)

        # Color swatch preview
        self._pk_swatch = QFrame()
        self._pk_swatch.setFixedSize(28, 22)
        self._pk_swatch.setStyleSheet("background:#ff0000;border:1px solid #444;border-radius:3px;")
        hcol.addWidget(self._pk_swatch)

        def _pk_chg(*_):
            r, g, b = self._pk_r.value(), self._pk_g.value(), self._pk_b.value()
            self._pk_swatch.setStyleSheet(
                f"background:#{r:02x}{g:02x}{b:02x};border:1px solid #444;border-radius:3px;"
            )
        self._pk_r.valueChanged.connect(_pk_chg)
        self._pk_g.valueChanged.connect(_pk_chg)
        self._pk_b.valueChanged.connect(_pk_chg)
        _pk_chg()

        # Color picker button (non-native for theme compat)
        btn_pick = QPushButton("Pick…")
        btn_pick.setFixedHeight(24)
        btn_pick.setStyleSheet(
            f"QPushButton{{background:{C_BG2};color:{C_CYAN};border:1px solid {C_BORDER};"
            f"border-radius:3px;padding:2px 8px;font-size:7pt;}}"
            f"QPushButton:hover{{background:{C_CYAN};color:{C_BG};}}"
        )
        def _open_picker():
            col = QColorDialog.getColor(
                QColor(self._pk_r.value(), self._pk_g.value(), self._pk_b.value()),
                page, "Pick Color",
                QColorDialog.ColorDialogOption.DontUseNativeDialog,
            )
            if col.isValid():
                self._pk_r.setValue(col.red())
                self._pk_g.setValue(col.green())
                self._pk_b.setValue(col.blue())
        btn_pick.clicked.connect(_open_picker)
        hcol.addWidget(btn_pick)

        # Quick-color presets — same palette as MANUAL page
        QC = [
            ("#ff0000","R",255,0,0), ("#00ff00","G",0,255,0), ("#0000ff","B",0,0,255),
            ("#00ffe0","C",0,255,224), ("#fe8019","O",254,128,25), ("#ffffff","W",255,255,255),
            ("#800080","P",128,0,128), ("#000000","—",0,0,0),
        ]
        for hx, lb, rv, gv, bv in QC:
            qb = QPushButton(lb)
            qb.setFixedSize(22, 18)
            qb.setFont(get_font(7, True))
            lum = rv + gv + bv
            qb.setStyleSheet(
                f"QPushButton{{background:{hx};"
                f"color:{'#000' if lum > 380 else '#eee'};"
                f"border:1px solid #333;border-radius:2px;padding:0;}}"
                f"QPushButton:hover{{border:2px solid {C_CYAN};}}"
            )
            qb.clicked.connect(
                lambda _, r=rv, g=gv, b=bv: [
                    self._pk_r.setValue(r), self._pk_g.setValue(g), self._pk_b.setValue(b)
                ]
            )
            hcol.addWidget(qb)

        hcol.addStretch()
        vlay.addWidget(g_color)

        # ── APPLY row ────────────────────────────────────────────────────────
        g_apply = QGroupBox(" APPLY ")
        happly = QHBoxLayout(g_apply)
        happly.setContentsMargins(8, 4, 8, 4)
        happly.setSpacing(6)

        self._pk_apply_btn = QPushButton("🎨 Apply to Selected")
        self._pk_apply_btn.setObjectName("apply_button")
        self._pk_apply_btn.setFixedHeight(26)
        self._pk_apply_btn.clicked.connect(self._perkey_apply_selected)
        happly.addWidget(self._pk_apply_btn)

        btn_full = QPushButton("⬛ Apply Full Layout")
        btn_full.setObjectName("run_button")
        btn_full.setFixedHeight(26)
        btn_full.clicked.connect(self._perkey_apply_full)
        happly.addWidget(btn_full)

        btn_clear = QPushButton("✕ Clear Keys")
        btn_clear.setObjectName("stop_button")
        btn_clear.setFixedHeight(26)
        btn_clear.clicked.connect(self._perkey_clear_keys)
        happly.addWidget(btn_clear)

        happly.addStretch()

        self._pk_status = SL("", color=C_DGREY, size=7)
        happly.addWidget(self._pk_status)
        vlay.addWidget(g_apply)

        # ── STATE row ─────────────────────────────────────────────────────────
        g_state = QGroupBox(" LAYOUT STATE ")
        hstate = QHBoxLayout(g_state)
        hstate.setContentsMargins(8, 4, 8, 4)
        hstate.setSpacing(6)

        _state_btn_style = (
            f"QPushButton{{background:{C_BG2};color:{C_YELLOW};border:1px solid {C_BORDER};"
            f"border-radius:3px;padding:2px 10px;font-size:7pt;font-weight:bold;}}"
            f"QPushButton:hover{{background:{C_YELLOW};color:{C_BG};}}"
        )
        for lbl, fn in [("💾 Save Layout", self._perkey_save_layout),
                        ("📂 Load Layout", self._perkey_load_layout)]:
            b = QPushButton(lbl)
            b.setStyleSheet(_state_btn_style)
            b.setFixedHeight(24)
            b.clicked.connect(fn)
            hstate.addWidget(b)

        hstate.addWidget(
            SL("visual only — press Apply Full Layout to send to hardware",
               color=C_DGREY, size=6)
        )
        hstate.addStretch()
        vlay.addWidget(g_state)

        self._perkey_page = page
        self._perkey_update_sel_count()
        return page

    # ─── PER-KEY HELPER METHODS ─────────────────────────────────────────────

    def _perkey_update_sel_count(self):
        """Refresh the 'N selected' label."""
        if not hasattr(self, "_perkey_buttons") or not hasattr(self, "_perkey_sel_label"):
            return
        n = sum(1 for b in self._perkey_buttons.values() if b.isChecked())
        self._perkey_sel_label.setText(f"{n} selected")

    def _perkey_select_all(self, state: bool):
        for b in self._perkey_buttons.values():
            if b.isEnabled():
                b.setChecked(state)
        self._perkey_update_sel_count()

    def _perkey_invert_selection(self):
        for b in self._perkey_buttons.values():
            if b.isEnabled():
                b.setChecked(not b.isChecked())
        self._perkey_update_sel_count()

    def _perkey_double_click(self, btn: KeyButton):
        """Double-click: immediately apply current color to this one key."""
        r, g, b = self._pk_r.value(), self._pk_g.value(), self._pk_b.value()
        self._perkey_apply_keys([btn], r, g, b)

    def _perkey_apply_keys(self, buttons: list, r: int, g: int, b: int) -> int:
        """Queue setone commands for the given buttons.

        Skips keys whose current state already matches (r, g, b) — deduplicate.
        Returns the number of commands queued.
        """
        dev = str(self._perkey_dev_idx)
        rs, gs, bs = str(r), str(g), str(b)
        queued = 0
        for btn in buttons:
            if self._perkey_key_state.get(btn.key_name) == (r, g, b):
                continue  # dedupe
            for lid in btn.lightids:
                self._rgb_run(["setone", dev, str(lid), rs, gs, bs])
                queued += 1
            self._perkey_key_state[btn.key_name] = (r, g, b)
            btn.set_color((r, g, b))
        if queued and hasattr(self, "_pk_status"):
            self._pk_status.setText(f"queued {queued} cmd(s)")
        return queued

    def _perkey_apply_selected(self):
        """Apply current color to all selected (checked) keys."""
        r, g, b = self._pk_r.value(), self._pk_g.value(), self._pk_b.value()
        selected = [
            b for b in self._perkey_buttons.values()
            if b.isChecked() and b.isEnabled()
        ]
        if not selected:
            if hasattr(self, "_pk_status"):
                self._pk_status.setText("no keys selected")
            return
        self._perkey_apply_keys(selected, r, g, b)

    def _perkey_apply_full(self):
        """Send all keys that have an assigned color (boot/restore flow)."""
        dev = str(self._perkey_dev_idx)
        queued = 0
        for key_name, color in self._perkey_key_state.items():
            btn = self._perkey_buttons.get(key_name)
            if btn is None or not btn.lightids:
                continue
            r, g, b = color
            for lid in btn.lightids:
                self._rgb_run(["setone", dev, str(lid), str(r), str(g), str(b)])
                queued += 1
        if hasattr(self, "_pk_status"):
            self._pk_status.setText(f"queued {queued} cmd(s) — full layout")

    def _perkey_clear_keys(self):
        """Send black to selected keys (or all if none selected), clear state."""
        selected = [
            b for b in self._perkey_buttons.values()
            if b.isChecked() and b.isEnabled()
        ]
        targets = (
            selected if selected
            else [b for b in self._perkey_buttons.values() if b.isEnabled()]
        )
        dev = str(self._perkey_dev_idx)
        for btn in targets:
            for lid in btn.lightids:
                self._rgb_run(["setone", dev, str(lid), "0", "0", "0"])
            self._perkey_key_state.pop(btn.key_name, None)
            btn.set_color(None)
        if hasattr(self, "_pk_status"):
            self._pk_status.setText(f"cleared {len(targets)} key(s)")

    def _perkey_save_layout(self):
        """Persist per-key color state and mark it as the active RGB source.

        Sets active=True in rgb_perkey.json and clears the manual default
        profile so the tray and this flag stay mutually exclusive.
        """
        path = Path.home() / ".config/ryzenadj_gui/rgb_perkey.json"
        m = self._perkey_mappings or {}
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            data = {
                "schema": 1,
                "active": True,               # tray will load this at boot
                "dev_vidpid": [m.get("vid", 0), m.get("pid", 0)],
                "keys": {k: list(v) for k, v in self._perkey_key_state.items()},
            }
            path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
            msg = f"saved {len(data['keys'])} keys  ·  active at boot ⌨"
            if hasattr(self, "_pk_status"):
                self._pk_status.setText(msg)
            self._log(f"[perkey] layout saved ({len(data['keys'])} keys, active=True) → {path}")
        except Exception as e:
            self._log(f"[perkey] save error: {e}")
            if hasattr(self, "_pk_status"):
                self._pk_status.setText(f"save error: {e}")
            return  # don't touch the manual default on failure

        # ── Mutual exclusion: clear manual profile default ─────────────────
        if self._rgb_default_name:
            self._rgb_default_name = ""
            self._save_rgb_profiles_data()
            self._rgb_refresh_profile_combo()
            self._log("[perkey] cleared manual RGB default (per-key is now active)")

    def _perkey_load_layout(self):
        """Load per-key state from disk (visual update only, no hardware send)."""
        path = Path.home() / ".config/ryzenadj_gui/rgb_perkey.json"
        if not path.exists():
            if hasattr(self, "_pk_status"):
                self._pk_status.setText("no saved layout found")
            return
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            keys = data.get("keys", {})
            # Reset all buttons first
            for btn in self._perkey_buttons.values():
                btn.set_color(None)
            self._perkey_key_state.clear()
            # Apply loaded colors
            for key_name, rgb in keys.items():
                if isinstance(rgb, list) and len(rgb) == 3:
                    color = (int(rgb[0]), int(rgb[1]), int(rgb[2]))
                    self._perkey_key_state[key_name] = color
                    btn = self._perkey_buttons.get(key_name)
                    if btn:
                        btn.set_color(color)
            msg = f"loaded {len(keys)} keys — press Apply Full Layout to send"
            if hasattr(self, "_pk_status"):
                self._pk_status.setText(msg)
            self._log(f"[perkey] {msg}")
        except Exception as e:
            self._log(f"[perkey] load error: {e}")
            if hasattr(self, "_pk_status"):
                self._pk_status.setText(f"load error: {e}")

    def _perkey_reload(self):
        """Rebuild the Per-Key page (re-reads mappings.json)."""
        if not hasattr(self, "_rgb_subtabs"):
            return
        old_page = getattr(self, "_perkey_page", None)
        old_idx = self._rgb_subtabs.indexOf(old_page) if old_page else -1
        if old_idx >= 0:
            self._rgb_subtabs.removeTab(old_idx)
            if old_page:
                old_page.deleteLater()
        perkey_page = self._build_rgb_perkey_page()
        self._rgb_subtabs.addTab(perkey_page, "  ⌨ PER-KEY  ")
        self._rgb_subtabs.setCurrentWidget(perkey_page)

    # ─── RGB PROFILE PERSISTENCE ─────────────────────────────────────────
    def _load_rgb_profiles_data(self):
        """Loads RGB profile data from disk."""
        try:
            if self._rgb_profiles_path.exists():
                data = json.loads(self._rgb_profiles_path.read_text(encoding="utf-8"))
                self._rgb_profiles = data.get("profiles", {})
                self._rgb_default_name = data.get("default", "")
        except Exception:
            self._rgb_profiles = {}
            self._rgb_default_name = ""

    def _save_rgb_profiles_data(self):
        """Writes the RGB profile data to disk."""
        try:
            self._rgb_profiles_path.parent.mkdir(parents=True, exist_ok=True)
            data = {"profiles": self._rgb_profiles, "default": self._rgb_default_name}
            self._rgb_profiles_path.write_text(
                json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
        except Exception as e:
            self._log(f"❌ RGB profile save error: {e}")

    def _rgb_save_profile(self):
        """Saves the current command history as a profile."""
        if not self._rgb_cmd_history:
            self._log("⚠️ No RGB commands in history to save. Apply some settings first.")
            return
        name, ok = QInputDialog.getText(self, "Save RGB Profile", "Profile name:")
        if not ok or not name.strip():
            return
        name = name.strip()
        self._rgb_profiles[name] = {"commands": list(self._rgb_cmd_history)}
        if not self._rgb_default_name:
            self._rgb_default_name = name
        self._save_rgb_profiles_data()
        self._rgb_refresh_profile_combo()
        self._log(f"✅ RGB profile '{name}' saved ({len(self._rgb_cmd_history)} commands)")

    def _rgb_delete_profile(self):
        """Deletes the selected profile."""
        if not hasattr(self, '_rgb_profile_combo'):
            return
        name = self._rgb_profile_combo.currentData()
        if not name or name not in self._rgb_profiles:
            return
        del self._rgb_profiles[name]
        if self._rgb_default_name == name:
            self._rgb_default_name = next(iter(self._rgb_profiles), "")
        self._save_rgb_profiles_data()
        self._rgb_refresh_profile_combo()
        self._log(f"🗑️ RGB profile '{name}' deleted")

    def _rgb_load_profile(self):
        """Loads the selected profile — runs all commands in sequence."""
        if not hasattr(self, '_rgb_profile_combo'):
            return
        name = self._rgb_profile_combo.currentData()
        if not name or name not in self._rgb_profiles:
            return
        cmds = self._rgb_profiles[name].get("commands", [])
        if not cmds:
            self._log(f"⚠️ Profile '{name}' has no commands")
            return
        self._log(f"🔄 Loading RGB profile '{name}' ({len(cmds)} commands)...")
        self._rgb_cmd_history.clear()
        for args in cmds:
            self._rgb_run(args)

    def _rgb_set_default(self):
        """Makes the selected profile the default (boot) profile.

        Mutual exclusion: per-key layout's active flag is cleared so the
        tray doesn't try to load both sources at boot.
        """
        if not hasattr(self, '_rgb_profile_combo'):
            return
        name = self._rgb_profile_combo.currentData()
        if not name or name not in self._rgb_profiles:
            return
        self._rgb_default_name = name
        self._save_rgb_profiles_data()
        self._rgb_refresh_profile_combo()
        self._log(f"⭐ Default RGB profile set to '{name}'")

        # ── Mutual exclusion: deactivate per-key layout ────────────────────
        perkey_path = Path.home() / ".config/ryzenadj_gui/rgb_perkey.json"
        try:
            if perkey_path.exists():
                d = json.loads(perkey_path.read_text(encoding="utf-8"))
                if d.get("active"):
                    d["active"] = False
                    perkey_path.write_text(
                        json.dumps(d, indent=2, ensure_ascii=False), encoding="utf-8"
                    )
                    self._log("[rgb] per-key layout deactivated (manual profile is now default)")
                    if hasattr(self, "_pk_status"):
                        self._pk_status.setText(
                            f"deactivated — manual profile '{name}' is now default ⭐"
                        )
        except Exception as e:
            self._log(f"[rgb] could not update per-key active flag: {e}")

    def _rgb_clear_history(self):
        """Clears the command history."""
        self._rgb_cmd_history.clear()
        self._log("🗑️ RGB command history cleared")

    def _rgb_reset_all_settings(self):
        """Resets all RGB UI fields, turns off the lights, and clears the history."""
        # 1. Turn off all lights (setall 0 0 0)
        for d in range(max(1, self._rgb_dev_count)):
            self._rgb_run(["setall", "0", "0", "0"])
            self._rgb_run(["setdim", str(d), "255"])
        # 2. Clear the command history (don't save the setall/setdim reset commands)
        self._rgb_cmd_history.clear()
        # 3. Reset the UI widgets
        if hasattr(self, '_rgb_device'):
            self._rgb_device.setValue(0)
        if hasattr(self, '_rgb_one_from'):
            self._rgb_one_from.setValue(0)
        if hasattr(self, '_rgb_one_to'):
            self._rgb_one_to.setValue(0)
        if hasattr(self, '_rgb_r'):
            self._rgb_r.setValue(0)
        if hasattr(self, '_rgb_g'):
            self._rgb_g.setValue(0)
        if hasattr(self, '_rgb_b'):
            self._rgb_b.setValue(0)
        if hasattr(self, '_rgb_brightness'):
            self._rgb_brightness.setValue(255)
        if hasattr(self, '_slider_brightness'):
            self._slider_brightness.setValue(255)
        if hasattr(self, '_rgb_glob_type'):
            self._rgb_glob_type.setValue(0)
        if hasattr(self, '_rgb_glob_mode'):
            self._rgb_glob_mode.setValue(0)
        if hasattr(self, '_rgb_act_idx'):
            self._rgb_act_idx.setValue(0)
        if hasattr(self, '_rgb_tempo'):
            self._rgb_tempo.setValue(5)
        if hasattr(self, '_slider_tempo'):
            self._slider_tempo.setValue(5)
        if hasattr(self, '_rgb_length'):
            self._rgb_length.setValue(5)
        if hasattr(self, '_slider_length'):
            self._slider_length.setValue(5)
        # 4. Clear the animation rows (except the first two)
        if hasattr(self, '_action_row_data'):
            while len(self._action_row_data) > 0:
                cb, rs, gs, bs, rw = self._action_row_data.pop()
                rw.setParent(None)
                rw.deleteLater()
        self._log("🔴 All RGB settings reset")

    def _rgb_refresh_profile_combo(self):
        """Updates the profile combo box."""
        if not hasattr(self, '_rgb_profile_combo'):
            return
        combo = self._rgb_profile_combo
        combo.blockSignals(True)
        combo.clear()
        for name in sorted(self._rgb_profiles.keys()):
            prefix = "⭐ " if name == self._rgb_default_name else ""
            combo.addItem(f"{prefix}{name}", name)
        combo.blockSignals(False)

    def _find_alienfx_cli(self):
        """Finds the alienfx_cli path, or returns None if not found."""
        return find_tool("alienfx_cli")

    def _parse_rgb_device_count(self, output: str) -> int:
        """Parses the device count from the raw alienfx_cli output."""
        import re
        if not output.strip():
            return 0
        # P1: "Device #0", "Device #1" — most specific, works even on a single line
        m = re.findall(r'(?i)device\s+#\s*\d+', output)
        if m:
            return len(m)
        # P2: "2 low-level devices found" / "3 devices found" (number first)
        m = re.search(
            r'\b(\d{1,4})\s+(?:low[- ]level\s+|usb\s+|alienf[xy]\s+)?devices?\s+found',
            output, re.IGNORECASE)
        if m:
            return int(m.group(1))
        # P3: "Found N devices" (number after)
        m = re.search(r'found\s+(\d+)\s+devices?', output, re.IGNORECASE)
        if m:
            return int(m.group(1))
        # P4: "Device 0:" format at the start of a line
        m = re.findall(r'(?im)^\s*device\s+\d+', output)
        if m:
            return len(m)
        return 0

    def _kick_off_background_detection(self):
        """Startup perf fix A+B: runs the alienfx_cli device probe and the
        NVML init on daemon threads, so neither blocks the window from
        appearing. Each worker only does I/O/library calls — no Qt widget
        access — and reports back via a Signal, which Qt marshals onto the
        main thread automatically before the connected slot runs."""
        threading.Thread(target=self._rgb_detect_worker, daemon=True).start()
        threading.Thread(target=self._nvml_init_worker, daemon=True).start()

    def _rgb_detect_worker(self):
        """Background-thread body for the startup AlienFX device probe.
        Must not touch any Qt widget directly — only emit the result."""
        cli_path = self._alienfx_cli
        if not cli_path:
            self._rgb_detect_done.emit(0, "")
            return
        try:
            result = subprocess.run(
                [cli_path, "status"],
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                text=True, timeout=4
            )
            raw = result.stdout or ""
            count = self._parse_rgb_device_count(raw)
            self._rgb_detect_done.emit(count, raw)
        except Exception as e:
            self._rgb_detect_done.emit(0, f"error: {e}")

    def _on_rgb_detect_done(self, count: int, raw: str):
        """Main-thread slot: applies the async RGB detection result to the
        UI. Mirrors the widget-update logic in _rgb_redetect (the manual
        redetect button), so both paths behave identically."""
        self._rgb_dev_count = count
        if hasattr(self, '_rgb_device'):
            self._rgb_device.setMaximum(max(0, count - 1))
            if self._rgb_device.value() > max(0, count - 1):
                self._rgb_device.setValue(0)
        if hasattr(self, '_rgb_dev_hint'):
            if count > 0:
                hint = f"{count} device(s) found"
            else:
                hint = "alienfx_cli not found" if not self._alienfx_cli else "no devices detected"
            self._rgb_dev_hint.setText(hint)
        if hasattr(self, '_rgb_dev_raw') and raw:
            self._rgb_dev_raw.setText(raw.strip().replace('\n', ' ')[:90])

    def _nvml_init_worker(self):
        """Background-thread body for NVML init. Must not touch any Qt
        widget directly — only emit the result."""
        try:
            import pynvml as nvml
            nvml.nvmlInit()
            handle = nvml.nvmlDeviceGetHandleByIndex(0)
            self._nvml_init_done.emit(nvml, handle, "")
        except Exception as e:
            self._nvml_init_done.emit(None, None, str(e))

    def _on_nvml_init_done(self, nvml_module, handle, err: str):
        """Main-thread slot: applies the async NVML init result."""
        if nvml_module is not None and handle is not None:
            self._nvml = nvml_module
            self.nvml_handle = handle
            self.nvml_available = True
            self._log("✅ NVML initialized")
        else:
            self._nvml = None
            self.nvml_handle = None
            self.nvml_available = False
            self._log(f"⚠️ NVML not available: {err}")

    def _get_rgb_device_count(self) -> int:
        """Detects the device count at application startup.

        Kept for backward compatibility (e.g. if any code path still wants
        a synchronous count); normal startup no longer calls this — see
        _kick_off_background_detection / _rgb_detect_worker instead."""
        import subprocess
        cli_path = self._find_alienfx_cli()
        if not cli_path:
            return 0
        try:
            result = subprocess.run(
                [cli_path, "status"],
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                text=True, timeout=4
            )
            return self._parse_rgb_device_count(result.stdout or "")
        except Exception:
            return 0

    def _rgb_redetect(self):
        """⟳ button: re-detects the device count and shows the raw output."""
        import subprocess
        cli_path = self._find_alienfx_cli()
        if not cli_path:
            if hasattr(self, '_rgb_dev_hint'):
                self._rgb_dev_hint.setText("alienfx_cli not found")
            return
        try:
            result = subprocess.run(
                [cli_path, "status"],
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                text=True, timeout=5
            )
            raw = (result.stdout or "").strip().replace('\n', ' ')
            self._log(f"[alienfx] redetect stdout+stderr: {raw[:400] or '(empty)'}")
            if hasattr(self, '_rgb_dev_raw'):
                self._rgb_dev_raw.setText(raw[:90] or "(empty — check Dashboard log)")
            count = self._parse_rgb_device_count(result.stdout or "")
            self._rgb_dev_count = count
            if hasattr(self, '_rgb_device'):
                self._rgb_device.setMaximum(max(0, count - 1))
                if self._rgb_device.value() > max(0, count - 1):
                    self._rgb_device.setValue(0)
            hint = f"{count} device(s) found" if count > 0 else "no devices detected"
            if hasattr(self, '_rgb_dev_hint'):
                self._rgb_dev_hint.setText(hint)

        except Exception as e:
            self._log(f"[alienfx] redetect error: {e}")
            if hasattr(self, '_rgb_dev_hint'):
                self._rgb_dev_hint.setText(f"error: {e}")

    def _rgb_run(self, args: list):
        """Queues an alienfx_cli command; runs them in sequence.

        The USB HID device can only be opened by one process at a time.
        Concurrent calls return exit code 9 (device busy). That's why each
        command isn't started until the previous one finishes — a queue mechanism is used.
        """
        if not hasattr(self, '_alienfx_cli') or not self._alienfx_cli:
            self._log("❌ alienfx_cli not found. Install from: https://github.com/tr1xem/alienfx-linux")
            return

        # Add to the command history (for saving profiles)
        self._rgb_cmd_history.append([str(a) for a in args])

        # Add to the queue and start processing
        self._rgb_cmd_queue.append([str(a) for a in args])
        if not self._rgb_queue_busy:
            self._rgb_process_next()

    def _rgb_process_next(self):
        """Runs the next command from the queue."""
        if not self._rgb_cmd_queue:
            self._rgb_queue_busy = False
            return

        self._rgb_queue_busy = True
        args = self._rgb_cmd_queue.pop(0)

        cmd_str = self._alienfx_cli + " " + " ".join(args)
        self._log(f"[alienfx] ▶ {cmd_str}")

        proc = QProcess(self)
        proc.setProgram(self._alienfx_cli)
        proc.setArguments(args)
        proc.setProcessChannelMode(QProcess.MergedChannels)

        def _on_output():
            raw = bytes(proc.readAllStandardOutput()).decode("utf-8", errors="replace")
            for line in raw.splitlines():
                if line.strip():
                    self._log(f"[alienfx] {line}")

        def _on_finished(exit_code, exit_status):
            ok = (exit_code == 0)
            icon = "✅" if ok else "❌"
            self._log(f"[alienfx] {icon} exited: {exit_code}")
            # D12: release this process now that it has finished, rather than
            # leaving the last one pending until the next enqueue.
            if self._rgb_process is proc:
                self._rgb_process = None
            proc.deleteLater()
            # Run the next command (50ms delay — USB HID settle)
            QTimer.singleShot(50, self._rgb_process_next)

        proc.readyReadStandardOutput.connect(_on_output)
        proc.finished.connect(_on_finished)
        proc.start()
        self._rgb_process = proc

    # ─── TELEMETRY UPDATE ─────────────────────────────────────────────
    # B1+B7: setStyleSheet is one of the most expensive Qt operations; calling it
    # when the value hasn't changed triggers an unnecessary re-polish.
    # Fix: cache the last color for each bar and tele label.
    _tele_last_color    = None          # B7
    _bar_last_colors    = None          # B1  (list, same length as thread_bars)
    _bar_last_id_colors = None          # B1

    def _tele_update(self, status, color, active_profile, cpu_params, usage_pcts, watt, avg_freq):
        # D3: skip painting if the window is hidden (the worker keeps running)
        if not self.isVisible():
            return

        self.tele.setText(status)
        # B7: write the tele style if its color changed
        if color != self._tele_last_color:
            self._tele_last_color = color
            self.tele.setStyleSheet(
                f"QLabel{{background-color:{C_BG2};padding:2px 6px;border-radius:3px;color:{color};}}"
            )

        # ✅ Remove the G-MODE override: use active_profile directly
        if active_profile and active_profile != "UNKNOWN":
            self._active_profile = active_profile.lower()
        # If active_profile isn't provided, keep the current value

        self._update_profile_buttons()
        self.lbl_driver.setText(f"Drv: {cpu_params['driver']}")
        self.lbl_gov.setText(f"Gov: {cpu_params['gov']}")
        self.lbl_epp.setText(f"EPP: {cpu_params['epp']}")
        self.lbl_power.setText(f"CPU Pwr: {watt:.1f} W")
        self.lbl_freq.setText(f"Avg Frq: {avg_freq:.0f} MHz")

        # B1: quantize each bar's color into 8 buckets and cache it;
        # only write setStyleSheet when the bucket changes (~48 per tick → only as many as changed)
        n_bars = len(self.thread_bars)
        if self._bar_last_colors is None or len(self._bar_last_colors) != n_bars:
            self._bar_last_colors    = [None] * n_bars
            self._bar_last_id_colors = [None] * n_bars

        for i, (lbl_id, lbl_pct, bar) in enumerate(self.thread_bars):
            val = int(usage_pcts[i]) if i < len(usage_pcts) else 0
            bar.setValue(val)

            # 8-bucket color quantization (0-12, 12-25, 25-37, 37-50, 50-62, 62-75, 75-87, 87-100)
            bucket = min(7, val * 8 // 101)
            if bucket != self._bar_last_colors[i]:
                self._bar_last_colors[i] = bucket
                # Compute the actual color
                if val <= 20:
                    t = val / 20.0
                    r, g, b = 255, int(255 - t * 65), int(255 - t * 255)
                elif val <= 60:
                    t = (val - 20) / 40.0
                    r, g, b = 255, int(190 - t * 90), 0
                else:
                    t = (val - 60) / 40.0
                    r, g, b = 255, int(100 - t * 100), 0
                c = f"#{r:02x}{g:02x}{b:02x}"
                bar.setStyleSheet(
                    f"QProgressBar{{border:1px solid {C_BORDER};background:{C_BG3};border-radius:2px;}}"
                    f"QProgressBar::chunk{{background:{c};border-radius:2px;}}"
                )
                lbl_pct.setStyleSheet(f"color:{c};")
            else:
                # the color didn't change but the number might have
                c = None  # setText alone is enough

            lbl_pct.setText(f"{val:2d}%")

            id_color = C_BLUE if val > 3 else C_VDGREY
            if id_color != self._bar_last_id_colors[i]:
                self._bar_last_id_colors[i] = id_color
                lbl_id.setStyleSheet(f"color:{id_color};")

    # ─── TAB TIMER WIRING (B3 + B4) ──────────────────────────────
    def _connect_tab_timers(self):
        """Ties the GPU and CPPC timers to tab visibility.
        Allows the dGPU to drop into D3cold while outside the GPU tab (B3).
        Stops unnecessary corefreq-cli forks while outside the CO tab (B4).
        """
        # Detect tab indices from the tab titles
        for i in range(self.tabs.count()):
            title = self.tabs.tabText(i)
            if "GPU" in title:
                self._gpu_tab_index = i
            elif "CURVE" in title or "OPTIMIZER" in title:
                self._co_tab_index = i

        self.tabs.currentChanged.connect(self._on_tab_changed)
        # Set the initial state
        self._on_tab_changed(self.tabs.currentIndex())

    def _on_tab_changed(self, index):
        """Turns the timers on/off based on the active tab."""
        gpu_visible = (index == getattr(self, '_gpu_tab_index', -1))
        co_visible  = (index == getattr(self, '_co_tab_index',  -1))

        # GPU timer (B3)
        if gpu_visible:
            if not self.gpu_info_timer.isActive():
                self.gpu_info_timer.start()
                self._update_gpu_info()   # an immediate update
            # Auto "Read Current Curve" once per tab-open, only on the
            # transition into visibility (not on every timer tick / repeated
            # calls while the tab stays active).
            if not getattr(self, '_gpu_tab_was_visible', False):
                self._read_gpu_curve()
        else:
            if self.gpu_info_timer.isActive():
                self.gpu_info_timer.stop()
        self._gpu_tab_was_visible = gpu_visible

        # CPPC timer (B4)
        if co_visible:
            if not self._cppc_timer.isActive():
                self._cppc_timer.start()
                self._fetch_cppc()        # an immediate update
        else:
            if self._cppc_timer.isActive():
                self._cppc_timer.stop()

        # CO Live timer (sysfs — 1s, subprocess yok)
        if co_visible:
            if not self._co_live_timer.isActive():
                self._co_live_timer.start()
                self._update_co_live()    # an immediate first update
        else:
            if self._co_live_timer.isActive():
                self._co_live_timer.stop()

    # ─── CLOSE ─────────────────────────────────────────────────────────
    def closeEvent(self, e):
        if self._cf_timer:
            self._cf_timer.stop()
        if self._rgb_process is not None:
            if self._rgb_process.state() == QProcess.Running:
                self._rgb_process.kill()
            self._rgb_process.deleteLater()
            self._rgb_process = None
        if self._cf_process and self._cf_process.state() == QProcess.Running:
            self._cf_process.kill()
        if self._co_live_timer:
            self._co_live_timer.stop()
        for _fh in getattr(self, '_co_live_handles', {}).values():
            if _fh:
                try:
                    _fh.close()
                except Exception:
                    pass
        if self._cppc_timer:
            self._cppc_timer.stop()
        if self.gpu_info_timer:
            self.gpu_info_timer.stop()
        if self.root_process and self.root_process.state() == QProcess.Running:
            self.root_process.kill()
            self.root_process.deleteLater()
        if hasattr(self, '_reload_process') and self._reload_process is not None:
            if self._reload_process.state() == QProcess.Running:
                self._reload_process.kill()
        if self.gmode_process is not None:
            if self.gmode_process.state() == QProcess.Running:
                self.gmode_process.kill()
            self.gmode_process.deleteLater()
            self.gmode_process = None
        if hasattr(self, "tele_thread"):
            self.tele_thread.stop()
            self.tele_thread.wait(2500)  # D4: 300 ms wasn't enough; safe margin for the 2s sleep loop
        if hasattr(self, 'nvml_available') and self.nvml_available:
            try:
                import pynvml as nvml
                nvml.nvmlShutdown()
            except Exception:
                pass
        e.accept()

# ─── MAIN ────────────────────────────────────────────────────────────────
def main():
    _check_not_root()  # A5: activate the root protection
    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(True)
    app.setFont(get_font(8))
    w = RyzenAdjGUI()
    w.show()
    sys.exit(app.exec())

if __name__ == "__main__":
    main()
