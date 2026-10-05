import sys
import json
import os
import base64
import io
import threading
from datetime import datetime

from pynput import keyboard as pynput_keyboard

from PyQt6.QtWidgets import (
    QApplication, QWidget, QLabel, QPushButton,
    QVBoxLayout, QComboBox, QFrame, QHBoxLayout,
    QSizePolicy, QInputDialog, QCheckBox, QDialog, QLineEdit,
    QSpinBox, QFormLayout, QDialogButtonBox, QListWidget, QListWidgetItem,
    QMessageBox
)
from PyQt6.QtCore import Qt, QRect, QPoint, pyqtSignal, QTimer, QSize
from PyQt6.QtGui import QPainter, QPen, QColor, QPixmap, QImage, QIcon, QFont

try:
    import obsws_python
except ImportError:
    obsws_python = None

import mss
from PIL import Image


def resource_path(relative_path):
    try:
        base_path = sys._MEIPASS
    except Exception:
        base_path = os.path.abspath(".")
    return os.path.join(base_path, relative_path)


# User files live next to the app (or the .exe), whatever folder it is launched from
APP_DIR = os.path.dirname(sys.executable) if getattr(sys, "frozen", False) else os.path.dirname(os.path.abspath(__file__))
SETTINGS_FILE = os.path.join(APP_DIR, "settings.json")
PROFILES_FILE = os.path.join(APP_DIR, "profiles.json")
CAPTURES_FOLDER = os.path.join(APP_DIR, "captures")


def load_settings():
    if not os.path.exists(SETTINGS_FILE):
        return {}
    with open(SETTINGS_FILE, "r") as f:
        return json.load(f)


def save_settings(data):
    with open(SETTINGS_FILE, "w") as f:
        json.dump(data, f, indent=4)


def load_profiles():
    if not os.path.exists(PROFILES_FILE):
        return {}
    with open(PROFILES_FILE, "r") as f:
        return json.load(f)


def save_profiles(data):
    with open(PROFILES_FILE, "w") as f:
        json.dump(data, f, indent=4)


class KeyCaptureWindow(QWidget):
    key_selected = pyqtSignal(str)

    def __init__(self):
        super().__init__()
        self.setWindowTitle("Set Capture Key")
        self.setFixedSize(250, 100)
        self.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint)

        layout = QVBoxLayout()
        label = QLabel("Press any key...")
        label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(label)
        self.setLayout(layout)

        self.listener = pynput_keyboard.Listener(on_press=self.on_press)
        self.listener.start()

    def on_press(self, key):
        try:
            new_key = key.char
        except:
            new_key = key.name

        self.key_selected.emit(new_key)
        self.listener.stop()


class Overlay(QWidget):
    def __init__(self, parent, mode):
        super().__init__()
        self.parent = parent
        self.mode = mode

        self.start = QPoint()
        self.end = QPoint()

        self.setWindowFlags(Qt.WindowType.FramelessWindowHint)
        self.showFullScreen()
        self.setWindowOpacity(0.3)
        self.setStyleSheet("background-color: black;")
        self.setCursor(Qt.CursorShape.CrossCursor)

    def mousePressEvent(self, event):
        self.start = event.position().toPoint()
        self.end = self.start
        self.update()

    def mouseMoveEvent(self, event):
        self.end = event.position().toPoint()
        self.update()

    def mouseReleaseEvent(self, event):
        self.end = event.position().toPoint()

        rect = QRect(self.start, self.end)

        if self.mode == "square":
            size = min(abs(rect.width()), abs(rect.height()))
            rect.setWidth(size)
            rect.setHeight(size)

        screen = QApplication.screenAt(self.mapToGlobal(rect.center()))
        ratio = screen.devicePixelRatio()

        global_top_left = self.mapToGlobal(rect.topLeft())

        self.parent.capture_area = {
            "left": int(global_top_left.x() * ratio),
            "top": int(global_top_left.y() * ratio),
            "width": int(rect.width() * ratio),
            "height": int(rect.height() * ratio)
        }

        self.parent.save_current_settings()
        self.close()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setPen(QPen(QColor(255, 0, 0), 2))

        rect = QRect(self.start, self.end)

        if self.mode == "square":
            size = min(abs(rect.width()), abs(rect.height()))
            rect.setWidth(size)
            rect.setHeight(size)

        painter.drawRect(rect)


class GridOverlay(QLabel):
    def __init__(self, parent):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.grid_pixmap = None

    def set_grid_image(self, pixmap):
        self.grid_pixmap = pixmap
        self.update_grid()

    def update_grid(self):
        if self.grid_pixmap is None:
            self.clear()
            return

        scaled = self.grid_pixmap.scaled(
            self.width(),
            self.height(),
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation
        )

        self.setPixmap(scaled)

    def resizeEvent(self, event):
        self.update_grid()
        super().resizeEvent(event)


def pil_to_pixmap(img):
    img = img.convert("RGBA")
    data = img.tobytes("raw", "RGBA")
    qimg = QImage(data, img.width, img.height, QImage.Format.Format_RGBA8888)
    return QPixmap.fromImage(qimg.copy())


class ObsCapture:
    """Grabs an image of an OBS scene or source through OBS WebSocket."""

    def __init__(self):
        self.client = None
        self.client_key = None

    def _connect(self, cfg):
        if obsws_python is None:
            raise RuntimeError("Missing module obsws-python (py -m pip install obsws-python)")
        key = (cfg["host"], cfg["port"], cfg["password"])
        if self.client is None or self.client_key != key:
            self.client = obsws_python.ReqClient(
                host=cfg["host"], port=cfg["port"],
                password=cfg["password"], timeout=5
            )
            self.client_key = key
        return self.client

    def _send(self, cfg, request, payload=None):
        try:
            return self._connect(cfg).send(request, payload)
        except Exception:
            # Connection lost (OBS restarted...): retry once
            self.client = None
            return self._connect(cfg).send(request, payload)

    def list_sources(self, cfg):
        scenes = self._send(cfg, "GetSceneList").scenes
        names = [sc["sceneName"] for sc in reversed(scenes)]
        try:
            inputs = self._send(cfg, "GetInputList").inputs
            names += [i["inputName"] for i in inputs if i["inputName"] not in names]
        except Exception:
            pass
        return names

    def grab(self, cfg):
        resp = self._send(cfg, "GetSourceScreenshot", {
            "sourceName": cfg["source"],
            "imageFormat": "png",
            "imageCompressionQuality": -1,
        })
        data = resp.image_data.split(",", 1)[1]
        return Image.open(io.BytesIO(base64.b64decode(data))).convert("RGB")


class ObsSettingsDialog(QDialog):
    def __init__(self, parent, cfg, obs):
        super().__init__(parent)
        self.setWindowTitle("OBS WebSocket")
        self.obs = obs

        form = QFormLayout()
        self.host = QLineEdit(cfg["host"])
        self.port = QSpinBox()
        self.port.setRange(1, 65535)
        self.port.setValue(cfg["port"])
        self.password = QLineEdit(cfg["password"])
        self.password.setEchoMode(QLineEdit.EchoMode.Password)
        self.source = QComboBox()
        self.source.setEditable(True)
        if cfg["source"]:
            self.source.addItem(cfg["source"])

        form.addRow("Host", self.host)
        form.addRow("Port", self.port)
        form.addRow("Password", self.password)

        source_row = QHBoxLayout()
        source_row.addWidget(self.source, 1)
        btn_refresh = QPushButton("Refresh")
        btn_refresh.clicked.connect(self.refresh)
        source_row.addWidget(btn_refresh)
        form.addRow("Scene / Source", source_row)

        self.status = QLabel("")
        self.status.setWordWrap(True)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)

        layout = QVBoxLayout()
        layout.addLayout(form)
        layout.addWidget(self.status)
        layout.addWidget(buttons)
        self.setLayout(layout)
        self.resize(380, 200)

    def config(self):
        return {
            "host": self.host.text().strip() or "localhost",
            "port": self.port.value(),
            "password": self.password.text(),
            "source": self.source.currentText().strip(),
        }

    def refresh(self):
        try:
            names = self.obs.list_sources(self.config())
        except Exception as e:
            self.status.setText(f"Cannot connect: {e}")
            return
        current = self.source.currentText()
        self.source.clear()
        self.source.addItems(names)
        if current in names:
            self.source.setCurrentText(current)
        self.status.setText(f"Connected: {len(names)} scenes/sources found")


class CropCanvas(QWidget):
    """Shows the OBS image and lets the user draw the map area."""

    def __init__(self, image, mode, initial):
        super().__init__()
        self.image = image
        self.mode = mode
        self.pixmap = pil_to_pixmap(image)
        self.sel = initial  # fractions (x, y, w, h) or None
        self.drag_start = None
        self.setMinimumSize(480, 270)
        self.setCursor(Qt.CursorShape.CrossCursor)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)

    def keyPressEvent(self, event):
        # Arrows: move by 1 pixel / Shift + arrows: resize by 1 pixel
        if not self.sel:
            return
        iw, ih = self.image.size
        dx, dy = 1 / iw, 1 / ih
        x, y, w, h = self.sel
        key = event.key()
        resize = bool(event.modifiers() & Qt.KeyboardModifier.ShiftModifier)
        moves = {
            Qt.Key.Key_Left: (-dx, 0), Qt.Key.Key_Right: (dx, 0),
            Qt.Key.Key_Up: (0, -dy), Qt.Key.Key_Down: (0, dy),
        }
        if key not in moves:
            super().keyPressEvent(event)
            return
        mx, my = moves[key]
        if resize:
            w, h = max(w + mx, dx), max(h + my, dy)
        else:
            x = min(max(x + mx, 0.0), 1.0 - w)
            y = min(max(y + my, 0.0), 1.0 - h)
        self.sel = (x, y, w, h)
        self.update()

    def image_rect(self):
        scaled = self.pixmap.size().scaled(self.size(), Qt.AspectRatioMode.KeepAspectRatio)
        x = (self.width() - scaled.width()) // 2
        y = (self.height() - scaled.height()) // 2
        return QRect(x, y, scaled.width(), scaled.height())

    def to_frac(self, pos):
        r = self.image_rect()
        fx = min(max((pos.x() - r.x()) / r.width(), 0.0), 1.0)
        fy = min(max((pos.y() - r.y()) / r.height(), 0.0), 1.0)
        return fx, fy

    def mousePressEvent(self, event):
        self.drag_start = self.to_frac(event.position().toPoint())
        self.sel = None
        self.update()

    def mouseMoveEvent(self, event):
        if self.drag_start is None:
            return
        x0, y0 = self.drag_start
        x1, y1 = self.to_frac(event.position().toPoint())
        w, h = abs(x1 - x0), abs(y1 - y0)
        x = x0 if x1 >= x0 else x0 - w
        y = y0 if y1 >= y0 else y0 - h
        self.sel = (x, y, w, h)
        self.update()

    def mouseReleaseEvent(self, event):
        self.drag_start = None

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor(34, 34, 34))
        r = self.image_rect()
        painter.drawPixmap(r, self.pixmap)
        if self.sel:
            x, y, w, h = self.sel
            painter.setPen(QPen(QColor(255, 0, 0), 1))
            painter.drawRect(QRect(
                int(r.x() + x * r.width()), int(r.y() + y * r.height()),
                int(w * r.width()), int(h * r.height())
            ))


class CropDialog(QDialog):
    def __init__(self, parent, image, mode, initial):
        super().__init__(parent)
        self.setWindowTitle("Select the map area (empty = whole image)")
        self.canvas = CropCanvas(image, mode, initial)
        hint = QLabel(
            "Draw a rectangle that follows the edge of the map exactly. "
            "Arrows: move by 1 px  -  Shift + arrows: grow/shrink by 1 px  -  "
            "Enlarge the window for more precision."
        )
        hint.setWordWrap(True)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        btn_full = buttons.addButton("Whole image", QDialogButtonBox.ButtonRole.ResetRole)
        btn_full.clicked.connect(self.clear_selection)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)

        layout = QVBoxLayout()
        layout.addWidget(self.canvas, 1)
        layout.addWidget(hint)
        layout.addWidget(buttons)
        self.setLayout(layout)
        self.resize(960, 640)
        self.setWindowFlag(Qt.WindowType.WindowMaximizeButtonHint, True)
        self.canvas.setFocus()

    def clear_selection(self):
        self.canvas.sel = None
        self.canvas.update()

    def selection(self):
        sel = self.canvas.sel
        if sel and sel[2] > 0.005 and sel[3] > 0.005:
            return list(sel)
        return None


# ====================================================================== acre recognition
STATUS_STYLE = {
    "ok": (None, "Confident"),
    "check": (QColor(255, 165, 0), "Worth a check"),
    "uncertain": (QColor(230, 40, 40), "Uncertain"),
    "in_game": (QColor(190, 80, 255), "48 / 49 / 50: check in game"),
    "confirmed": (QColor(80, 160, 255), "Picked by you"),
}
MINI_COLORS = {0: (90, 170, 33), 7: (74, 150, 33), 1: (8, 93, 206), 2: (214, 215, 115),
               3: (181, 182, 181), 4: (255, 142, 82), 5: (140, 69, 8)}


def mini_pixmap(mini, size=72):
    import numpy as np
    a = np.zeros((48, 48, 3), np.uint8)
    for k, c in MINI_COLORS.items():
        a[mini == k] = c
    return pil_to_pixmap(Image.fromarray(a).resize((size, size), Image.NEAREST))


class MapView(QWidget):
    """Capture + 5x5 grid, click a cell to pick its acre."""
    cell_clicked = pyqtSignal(int, int)

    def __init__(self, image, dialog):
        super().__init__()
        self.pixmap = pil_to_pixmap(image)
        self.dialog = dialog
        self.selected = (0, 0)
        self.setMinimumSize(420, 420)

    def map_rect(self):
        side = min(self.width(), self.height())
        return QRect((self.width() - side) // 2, (self.height() - side) // 2, side, side)

    def mousePressEvent(self, event):
        r = self.map_rect()
        pos = event.position().toPoint()
        if not r.contains(pos):
            return
        gx = min(int((pos.x() - r.x()) * 5 / r.width()), 4)
        gy = min(int((pos.y() - r.y()) * 5 / r.height()), 4)
        self.selected = (gy, gx)
        self.cell_clicked.emit(gy, gx)
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        r = self.map_rect()
        painter.drawPixmap(r, self.pixmap)
        cw = r.width() / 5
        painter.setPen(QPen(QColor(0, 0, 0, 120), 1))
        for i in range(1, 5):
            painter.drawLine(int(r.x() + i * cw), r.y(), int(r.x() + i * cw), r.bottom())
            painter.drawLine(r.x(), int(r.y() + i * cw), r.right(), int(r.y() + i * cw))
        font = QFont(); font.setPointSize(8); font.setBold(True)
        painter.setFont(font)
        for gy in range(5):
            for gx in range(5):
                cell = QRect(int(r.x() + gx * cw), int(r.y() + gy * cw), int(cw), int(cw))
                color = STATUS_STYLE[self.dialog.cell_status(gy, gx)][0]
                if color:
                    painter.setPen(QPen(color, 3))
                    painter.drawRect(cell.adjusted(2, 2, -2, -2))
                name = self.dialog.cell_name(gy, gx)
                label = QRect(cell.x() + 3, cell.y() + 3, int(cw) - 6, 16)
                painter.fillRect(label, QColor(0, 0, 0, 150))
                painter.setPen(QColor(255, 255, 255))
                painter.drawText(label.adjusted(3, 0, 0, 0), Qt.AlignmentFlag.AlignVCenter, name)
        gy, gx = self.selected
        painter.setPen(QPen(QColor(255, 255, 255), 3))
        painter.drawRect(QRect(int(r.x() + gx * cw), int(r.y() + gy * cw), int(cw), int(cw)))


class ResultsDialog(QDialog):
    def __init__(self, parent, analyzer, image, res):
        super().__init__(parent)
        self.setWindowTitle("Acre recognition")
        self.analyzer = analyzer
        self.res = res
        self.assign = dict(res["assign"])
        self.confirmed = set()
        self.learned = False

        self.view = MapView(image, self)
        self.view.cell_clicked.connect(self.show_cell)

        self.cell_title = QLabel()
        self.cell_title.setStyleSheet("font-size: 15px; font-weight: bold;")
        self.cell_info = QLabel()
        self.cell_info.setWordWrap(True)
        self.cand_list = QListWidget()
        self.cand_list.setIconSize(QSize(72, 72))
        self.cand_list.itemClicked.connect(self.choose)

        right = QVBoxLayout()
        right.addWidget(self.cell_title)
        right.addWidget(self.cell_info)
        right.addWidget(QLabel("Click the right acre:"))
        right.addWidget(self.cand_list, 1)

        top = QHBoxLayout()
        top.addWidget(self.view, 3)
        top.addLayout(right, 2)

        n_check = sum(1 for p in res["status"] if res["status"][p] != "ok")
        self.summary = QLabel()
        self.summary.setWordWrap(True)

        btn_copy = QPushButton("Confirm and copy for the sheet")
        btn_copy.setStyleSheet("font-weight: bold; padding: 6px;")
        btn_copy.clicked.connect(self.copy_code)
        btn_close = QPushButton("Close")
        btn_close.clicked.connect(self.close)
        bottom = QHBoxLayout()
        bottom.addWidget(self.summary, 1)
        bottom.addWidget(btn_close)
        bottom.addWidget(btn_copy)

        layout = QVBoxLayout()
        layout.addLayout(top, 1)
        layout.addLayout(bottom)
        self.setLayout(layout)
        self.resize(980, 640)

        # open on the first cell that needs a check
        first = next(((gy, gx) for gy in range(5) for gx in range(5)
                      if res["status"][(gy, gx)] != "ok"), (0, 0))
        self.view.selected = first
        self.show_cell(*first)
        self.update_summary()

    def cell_status(self, gy, gx):
        if (gy, gx) in self.confirmed:
            return "confirmed"
        return self.res["status"][(gy, gx)]

    def cell_name(self, gy, gx):
        return self.analyzer.cat.entries[self.assign[(gy, gx)]]["name"]

    def update_summary(self):
        counts = {}
        for gy in range(5):
            for gx in range(5):
                st = self.cell_status(gy, gx)
                counts[st] = counts.get(st, 0) + 1
        words = {"check": "worth a check (orange)", "uncertain": "uncertain (red)",
                 "in_game": "to check in game, 48/49/50 (purple)"}
        parts = [f"{counts[k]} {words[k]}" for k in ("check", "uncertain", "in_game") if counts.get(k)]
        self.summary.setText("Everything looks good." if not parts else "Acres: " + ", ".join(parts) + ".")

    def show_cell(self, gy, gx):
        cat = self.analyzer.cat.entries
        self.cell_title.setText(f"Acre {'ABCDE'[gy]}{gx + 1}")
        self.cell_info.setText(STATUS_STYLE[self.cell_status(gy, gx)][1])
        self.cand_list.clear()
        current = self.assign[(gy, gx)]
        for n in self.res["cand"][(gy, gx)]:
            e = cat[n]
            text = f"{e['name']}\n{e['label']}" + ("  (bridge)" if e["bridge"] else "")
            item = QListWidgetItem(QIcon(mini_pixmap(e["mini"])), text)
            item.setData(Qt.ItemDataRole.UserRole, n)
            self.cand_list.addItem(item)
            if n == current:
                item.setSelected(True)
                self.cand_list.setCurrentItem(item)

    def choose(self, item):
        p = self.view.selected
        self.assign[p] = item.data(Qt.ItemDataRole.UserRole)
        self.confirmed.add(p)
        self.cell_info.setText(STATUS_STYLE["confirmed"][1])
        self.view.update()
        self.update_summary()

    def copy_code(self):
        pending = [p for p in self.res["status"]
                   if self.res["status"][p] == "in_game" and p not in self.confirmed]
        if pending:
            names = ", ".join(f"{'ABCDE'[gy]}{gx + 1}" for gy, gx in pending)
            ok = QMessageBox.question(
                self, "Acres to check",
                f"{names}: 48, 49 and 50 look identical on the map.\n"
                "Copy anyway? (you can still fix it in the sheet)")
            if ok != QMessageBox.StandardButton.Yes:
                return
        QApplication.clipboard().setText(self.analyzer.sheet_code(self.assign))
        if not self.learned:
            # every confirmed acre becomes a reference for the next maps
            confirmed_assign = {p: n for p, n in self.assign.items()}
            for p in self.confirmed:
                self.res["status"][p] = "ok"
            try:
                self.analyzer.confirm(self.res, confirmed_assign)
                self.learned = True
            except Exception as e:
                QMessageBox.warning(self, "Error", f"References not saved: {e}")
        self.summary.setText("Code copied! In the sheet: menu TownHelper > Import map, then paste (Ctrl+V).")


class CaptureTool(QWidget):
    # The hotkey is listened to in another thread: this signal makes the
    # capture run in the UI thread.
    capture_requested = pyqtSignal()
    obs_image_ready = pyqtSignal(object, str)
    analysis_done = pyqtSignal(object, object, str)

    def __init__(self):
        super().__init__()

        self.settings = load_settings()
        self.profiles = load_profiles()

        self.capture_area = self.settings.get("capture_area")
        self.capture_mode = self.settings.get("mode", "square")
        self.grid_type = self.settings.get("grid", "None")
        self.capture_key = self.settings.get("key", "f8")
        self.auto_save = self.settings.get("auto_save", False)
        self.capture_source = self.settings.get("capture_source", "Screen")
        self.obs_config = {
            "host": "localhost", "port": 4455, "password": "", "source": ""
        }
        self.obs_config.update(self.settings.get("obs", {}))
        self.obs_crop = self.settings.get("obs_crop")
        self.obs = ObsCapture()
        self.obs_busy = False

        self.raw_image = None
        self.last_saved_image = None

        self.grid_images = {
            "Wild World": QPixmap(resource_path("wwgrid.png")),
            "City Folk": QPixmap(resource_path("cfgrid.png"))
        }

        self.setWindowTitle("ACCT TownHelper")
        self.setWindowIcon(QIcon(resource_path("icon.ico")))
        self.resize(350, 450)
        self.setMinimumSize(200, 150)
        self.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint)

        self.init_ui()
        self.capture_requested.connect(self.capture_screen)
        self.obs_image_ready.connect(self.on_obs_image)
        self.analysis_done.connect(self.on_analysis_done)
        self.analyzer = None
        self.analysing = False
        self.start_listener()

    def init_ui(self):
        layout = QVBoxLayout()

        self.settings_button = QPushButton("Settings")
        self.settings_button.clicked.connect(self.toggle_settings)
        layout.addWidget(self.settings_button)

        action_layout = QHBoxLayout()

        btn_area = QPushButton("Select Area")
        btn_area.clicked.connect(self.select_area)
        action_layout.addWidget(btn_area)

        btn_test = QPushButton("Test")
        btn_test.clicked.connect(self.capture_screen)
        btn_test.setMaximumWidth(60)
        action_layout.addWidget(btn_test)

        btn_save = QPushButton("Save")
        btn_save.setToolTip("Save the last capture as PNG")
        btn_save.clicked.connect(self.save_capture)
        btn_save.setMaximumWidth(60)
        action_layout.addWidget(btn_save)

        btn_analyse = QPushButton("Analyze")
        btn_analyse.setToolTip("Identify the 25 acres of the captured map")
        btn_analyse.clicked.connect(self.analyse_capture)
        btn_analyse.setMaximumWidth(70)
        action_layout.addWidget(btn_analyse)

        layout.addLayout(action_layout)

        self.preview = QLabel()
        self.preview.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.preview.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.preview.setStyleSheet("background-color: #222;")
        layout.addWidget(self.preview)

        self.status_label = QLabel("")
        self.status_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.status_label.setStyleSheet("color: #8c8; font-size: 11px;")
        layout.addWidget(self.status_label)

        self.status_timer = QTimer(self)
        self.status_timer.setSingleShot(True)
        self.status_timer.timeout.connect(lambda: self.status_label.setText(""))

        self.setLayout(layout)

        self.grid_overlay = GridOverlay(self)

        self.settings_popup = QFrame(self)
        self.settings_popup.setStyleSheet("background-color: #333; border-radius: 8px;")
        self.settings_popup.setVisible(False)

        popup_layout = QVBoxLayout()

        self.source_menu = QComboBox()
        self.source_menu.addItems(["Screen", "OBS"])
        self.source_menu.setCurrentText(self.capture_source)
        self.source_menu.currentTextChanged.connect(self.set_capture_source)
        popup_layout.addWidget(self.source_menu)

        btn_obs = QPushButton("OBS settings")
        btn_obs.clicked.connect(self.open_obs_settings)
        popup_layout.addWidget(btn_obs)

        self.mode_menu = QComboBox()
        self.mode_menu.addItems(["square", "rectangle"])
        self.mode_menu.setCurrentText(self.capture_mode)
        self.mode_menu.currentTextChanged.connect(self.set_mode)
        popup_layout.addWidget(self.mode_menu)

        self.grid_menu = QComboBox()
        self.grid_menu.addItems(["None", "Wild World", "City Folk"])
        self.grid_menu.setCurrentText(self.grid_type)
        self.grid_menu.currentTextChanged.connect(self.set_grid)
        popup_layout.addWidget(self.grid_menu)

        self.key_button = QPushButton(f"Key: {self.capture_key.upper()}")
        self.key_button.clicked.connect(self.open_key_window)
        popup_layout.addWidget(self.key_button)

        self.profile_menu = QComboBox()
        self.profile_menu.addItems(["Profile", "Save Profile", "Load Profile"])
        self.profile_menu.currentTextChanged.connect(self.handle_profile)
        popup_layout.addWidget(self.profile_menu)

        self.auto_save_box = QCheckBox("Auto-save captures")
        self.auto_save_box.setStyleSheet("color: white;")
        self.auto_save_box.setChecked(self.auto_save)
        self.auto_save_box.toggled.connect(self.set_auto_save)
        popup_layout.addWidget(self.auto_save_box)

        btn_folder = QPushButton("Open captures folder")
        btn_folder.clicked.connect(self.open_captures_folder)
        popup_layout.addWidget(btn_folder)

        self.settings_popup.setLayout(popup_layout)

    def handle_profile(self, val):
        if val == "Save Profile":
            name, ok = QInputDialog.getText(self, "Save Profile", "Profile name:")
            if ok and name:
                self.save_profile(name)

        elif val == "Load Profile":
            if not self.profiles:
                return
            name, ok = QInputDialog.getItem(
                self, "Load Profile", "Choose profile:",
                list(self.profiles.keys()), 0, False
            )
            if ok:
                self.load_profile(name)

        self.profile_menu.setCurrentText("Profile")

    def save_profile(self, name):
        self.profiles[name] = {
            "capture_area": self.capture_area,
            "mode": self.capture_mode,
            "grid": self.grid_type,
            "key": self.capture_key,
            "capture_source": self.capture_source,
            "obs_source": self.obs_config["source"],
            "obs_crop": self.obs_crop,
            "window_size": [self.width(), self.height()]
        }
        save_profiles(self.profiles)

    def load_profile(self, name):
        profile = self.profiles.get(name)
        if not profile:
            return

        self.capture_area = profile.get("capture_area")
        self.capture_mode = profile.get("mode", "square")
        self.grid_type = profile.get("grid", "None")
        self.capture_key = profile.get("key", "f8")
        self.capture_source = profile.get("capture_source", "Screen")
        if profile.get("obs_source"):
            self.obs_config["source"] = profile["obs_source"]
        self.obs_crop = profile.get("obs_crop")
        self.source_menu.setCurrentText(self.capture_source)
        self.save_current_settings()

        size = profile.get("window_size", [350, 450])
        self.resize(size[0], size[1])

        self.mode_menu.setCurrentText(self.capture_mode)
        self.grid_menu.setCurrentText(self.grid_type)
        self.key_button.setText(f"Key: {self.capture_key.upper()}")

        if self.grid_type != "None":
            self.grid_overlay.set_grid_image(self.grid_images[self.grid_type])

    def resizeEvent(self, event):
        self.update_preview()
        self.update_grid_position()
        super().resizeEvent(event)

    def update_grid_position(self):
        self.grid_overlay.setGeometry(self.preview.geometry())

    def save_current_settings(self):
        save_settings({
            "capture_area": self.capture_area,
            "mode": self.capture_mode,
            "grid": self.grid_type,
            "key": self.capture_key,
            "auto_save": self.auto_save,
            "capture_source": self.capture_source,
            "obs": self.obs_config,
            "obs_crop": self.obs_crop
        })

    def toggle_settings(self):
        if self.settings_popup.isVisible():
            self.settings_popup.hide()
            return

        pos = self.settings_button.mapToGlobal(QPoint(0, self.settings_button.height()))
        self.settings_popup.move(self.mapFromGlobal(pos))
        self.settings_popup.resize(220, 330)
        self.settings_popup.show()
        self.settings_popup.raise_()

    def set_mode(self, val):
        self.capture_mode = val
        if val == "rectangle":
            self.grid_type = "None"
            self.grid_menu.setCurrentText("None")
            self.grid_overlay.set_grid_image(None)
        self.save_current_settings()

    def set_grid(self, val):
        if self.capture_mode != "square":
            self.grid_menu.setCurrentText("None")
            self.grid_overlay.set_grid_image(None)
            return

        self.grid_type = val
        if val == "None":
            self.grid_overlay.set_grid_image(None)
        else:
            self.grid_overlay.set_grid_image(self.grid_images[val])

        self.save_current_settings()

    def set_auto_save(self, checked):
        self.auto_save = checked
        self.save_current_settings()

    def show_status(self, text, color="#8c8"):
        self.status_label.setStyleSheet(f"color: {color}; font-size: 11px;")
        self.status_label.setText(text)
        self.status_timer.start(4000)

    def captures_folder(self):
        folder = os.path.abspath(CAPTURES_FOLDER)
        os.makedirs(folder, exist_ok=True)
        return folder

    def save_capture(self):
        if self.raw_image is None:
            self.show_status("No capture to save", "#e88")
            return
        if self.raw_image is self.last_saved_image:
            self.show_status("Capture already saved")
            return

        name = datetime.now().strftime("map_%Y-%m-%d_%H-%M-%S.png")
        path = os.path.join(self.captures_folder(), name)
        try:
            # PNG = lossless, required for colour calibration
            self.raw_image.save(path, "PNG")
        except Exception as e:
            self.show_status(f"Error: {e}", "#e88")
            return

        self.last_saved_image = self.raw_image
        self.show_status(f"Saved: {name}")

    def open_captures_folder(self):
        folder = self.captures_folder()
        try:
            os.startfile(folder)
        except Exception:
            self.show_status(folder)

    def start_listener(self):
        def on_press(key):
            try:
                if key.char == self.capture_key:
                    self.capture_requested.emit()
            except:
                if key.name == self.capture_key:
                    self.capture_requested.emit()

        self.listener = pynput_keyboard.Listener(on_press=on_press)
        self.listener.start()

    def open_key_window(self):
        self.key_window = KeyCaptureWindow()
        self.key_window.key_selected.connect(self.set_new_key)
        self.key_window.show()

    def set_new_key(self, key):
        self.capture_key = key
        self.key_button.setText(f"Key: {key.upper()}")
        self.key_window.close()
        self.save_current_settings()

    def set_capture_source(self, val):
        self.capture_source = val
        self.save_current_settings()

    def open_obs_settings(self):
        self.settings_popup.hide()
        dlg = ObsSettingsDialog(self, self.obs_config, self.obs)
        if dlg.exec():
            new_cfg = dlg.config()
            if new_cfg["source"] != self.obs_config["source"]:
                self.obs_crop = None  # new source: area must be selected again
            self.obs_config = new_cfg
            self.save_current_settings()

    def select_area(self):
        if self.capture_source == "OBS":
            self.select_obs_area()
            return
        self.overlay = Overlay(self, self.capture_mode)
        self.overlay.show()

    def select_obs_area(self):
        if not self.obs_config["source"]:
            self.show_status("Pick a scene in OBS settings first", "#e88")
            return
        try:
            full = self.obs.grab(self.obs_config)
        except Exception as e:
            self.show_status(f"OBS : {e}", "#e88")
            return
        dlg = CropDialog(self, full, self.capture_mode, self.obs_crop)
        if dlg.exec():
            self.obs_crop = dlg.selection()
            self.save_current_settings()
            self.raw_image = self.apply_crop(full)
            self.update_preview()

    def apply_crop(self, img):
        if not self.obs_crop:
            return img
        x, y, w, h = self.obs_crop
        W, H = img.size
        box = (round(x * W), round(y * H), round((x + w) * W), round((y + h) * H))
        out = img.crop(box)
        if self.capture_mode == "square" and out.width != out.height:
            # The game image does not use square pixels: the map is slightly
            # stretched on screen. Make it square again so it fits the grid.
            side = max(out.width, out.height)
            out = out.resize((side, side), Image.Resampling.LANCZOS)
        return out

    def capture_screen(self):
        if self.capture_source == "OBS":
            self.capture_obs()
            return

        if not self.capture_area:
            return

        with mss.mss() as sct:
            img = sct.grab(self.capture_area)
            self.raw_image = Image.frombytes("RGB", img.size, img.rgb)

        self.after_capture()

    def capture_obs(self):
        if not self.obs_config["source"]:
            self.show_status("Pick a scene in OBS settings first", "#e88")
            return
        if self.obs_busy:
            return
        self.obs_busy = True
        self.show_status("Capture OBS...", "#ccc")
        cfg = dict(self.obs_config)

        # The request takes ~1 s: run it off the UI thread so the app does not freeze
        def work():
            try:
                img = self.obs.grab(cfg)
                self.obs_image_ready.emit(img, "")
            except Exception as e:
                self.obs_image_ready.emit(None, str(e))

        threading.Thread(target=work, daemon=True).start()

    def on_obs_image(self, img, error):
        self.obs_busy = False
        if img is None:
            self.show_status(f"OBS : {error}", "#e88")
            return
        self.raw_image = self.apply_crop(img)
        self.status_label.setText("")
        self.after_capture()

    def analyse_capture(self):
        if self.raw_image is None:
            self.show_status("No capture to analyze", "#e88")
            return
        if self.analysing:
            return
        self.analysing = True
        self.show_status("Analyzing...", "#ccc")
        image = self.raw_image.copy()

        def work():
            try:
                if self.analyzer is None:
                    import acre_engine   # loaded on first analysis only
                    self.analyzer = acre_engine.Analyzer()
                res = self.analyzer.run(image)
                self.analysis_done.emit(image, res, "")
            except Exception as e:
                self.analysis_done.emit(None, None, str(e))

        threading.Thread(target=work, daemon=True).start()

    def on_analysis_done(self, image, res, error):
        self.analysing = False
        if res is None:
            self.show_status(f"Analysis: {error}", "#e88")
            return
        self.status_label.setText("")
        shown = image.convert("RGB").resize((910, 910), Image.Resampling.LANCZOS)
        self.results_dialog = ResultsDialog(self, self.analyzer, shown, res)
        self.results_dialog.show()

    def after_capture(self):
        self.update_preview()

        if self.auto_save:
            self.save_capture()

    def update_preview(self):
        if self.raw_image is None:
            return

        img = self.raw_image.convert("RGBA")
        data = img.tobytes("raw", "RGBA")

        qimg = QImage(data, img.width, img.height, QImage.Format.Format_RGBA8888)
        pixmap = QPixmap.fromImage(qimg)

        scaled = pixmap.scaled(
            self.preview.width(),
            self.preview.height(),
            Qt.AspectRatioMode.KeepAspectRatio
        )

        self.preview.setPixmap(scaled)

        self.update_grid_position()

        if self.grid_type != "None":
            self.grid_overlay.set_grid_image(self.grid_images[self.grid_type])

        self.grid_overlay.raise_()
        self.settings_popup.raise_()


app = QApplication(sys.argv)
window = CaptureTool()
window.show()
sys.exit(app.exec())