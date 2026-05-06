import os; os.environ.setdefault('PYTHONUTF8', '1')
"""
SweptPC v1.0.0
A free portable Windows PC cleanup tool by VaultSoft
Companion app to PulseMonitor
"""

import sys
import os
import shutil
import glob
import time
import tempfile
import subprocess
import winreg
from pathlib import Path
from datetime import datetime, timedelta

from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QLabel, QPushButton, QCheckBox, QProgressBar, QScrollArea,
    QFrame, QSizePolicy, QSpacerItem, QGraphicsDropShadowEffect,
    QSystemTrayIcon, QMenu
)
from PyQt6.QtCore import (
    Qt, QThread, pyqtSignal, QTimer, QPropertyAnimation,
    QEasingCurve, QRect, pyqtProperty, QPoint
)
from PyQt6.QtGui import (
    QColor, QPainter, QLinearGradient, QFont, QFontDatabase,
    QIcon, QPen, QBrush, QPalette, QPixmap, QCursor
)

APP_NAME    = "SweptPC"
APP_VERSION = "1.0.0"
BRAND       = "VaultSoft"
TEAL        = "#00D4AA"
TEAL_DIM    = "#00A882"
TEAL_GLOW   = "#00D4AA33"
BG_DARK     = "#0D1117"
BG_CARD     = "#161B22"
BG_HOVER    = "#1C2530"
BORDER      = "#21262D"
TEXT_MAIN   = "#E6EDF3"
TEXT_SUB    = "#7D8590"
TEXT_MUTED  = "#484F58"
RED_WARN    = "#F85149"
AMBER       = "#E3B341"

def get_folder_size(path):
    total = 0
    try:
        for dirpath, _, filenames in os.walk(path):
            for f in filenames:
                try:
                    fp = os.path.join(dirpath, f)
                    if not os.path.islink(fp):
                        total += os.path.getsize(fp)
                except (OSError, PermissionError):
                    pass
    except (OSError, PermissionError):
        pass
    return total

def format_bytes(size):
    for unit in ["B", "KB", "MB", "GB"]:
        if size < 1024:
            return f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} TB"

def safe_delete_path(path):
    freed = 0
    try:
        p = Path(path)
        if p.is_file() or p.is_symlink():
            freed = p.stat().st_size
            p.unlink(missing_ok=True)
        elif p.is_dir():
            freed = get_folder_size(str(p))
            shutil.rmtree(str(p), ignore_errors=True)
    except (OSError, PermissionError):
        pass
    return max(0, freed)

CLEANUP_TARGETS = {
    "windows_temp": {
        "label": "Windows Temp Files",
        "desc": "%TEMP% and C:\\Windows\\Temp",
        "icon": "[TMP]",
        "paths": [
            os.environ.get("TEMP", ""),
            os.environ.get("TMP", ""),
            r"C:\Windows\Temp",
        ],
    },
    "prefetch": {
        "label": "Prefetch Cache",
        "desc": "Windows app launch cache",
        "icon": "[PRE]",
        "paths": [r"C:\Windows\Prefetch"],
        "admin_required": True,
    },
    "windows_logs": {
        "label": "Windows Log Files",
        "desc": "Old CBS, setup and error logs",
        "icon": "[LOG]",
        "paths": [r"C:\Windows\Logs", r"C:\Windows\System32\LogFiles"],
        "pattern": "*.log",
        "older_than_days": 30,
    },
    "recycle_bin": {
        "label": "Recycle Bin",
        "desc": "All drives",
        "icon": "[BIN]",
        "special": "recycle_bin",
    },
    "chrome_cache": {
        "label": "Google Chrome Cache",
        "desc": "Browser cache data",
        "icon": "[WEB]",
        "paths": [
            os.path.join(os.environ.get("LOCALAPPDATA", ""), r"Google\Chrome\User Data\Default\Cache"),
            os.path.join(os.environ.get("LOCALAPPDATA", ""), r"Google\Chrome\User Data\Default\Code Cache"),
        ],
    },
    "edge_cache": {
        "label": "Microsoft Edge Cache",
        "desc": "Browser cache data",
        "icon": "[WEB]",
        "paths": [
            os.path.join(os.environ.get("LOCALAPPDATA", ""), r"Microsoft\Edge\User Data\Default\Cache"),
            os.path.join(os.environ.get("LOCALAPPDATA", ""), r"Microsoft\Edge\User Data\Default\Code Cache"),
        ],
    },
    "firefox_cache": {
        "label": "Firefox Cache",
        "desc": "Browser cache data",
        "icon": "[WEB]",
        "paths": [os.path.join(os.environ.get("LOCALAPPDATA", ""), r"Mozilla\Firefox\Profiles")],
        "subfolders": ["cache2", "startupCache", "thumbnails"],
    },
    "teams_cache": {
        "label": "Microsoft Teams Cache",
        "desc": "Teams app cache",
        "icon": "[MSG]",
        "paths": [
            os.path.join(os.environ.get("APPDATA", ""), r"Microsoft\Teams\Cache"),
            os.path.join(os.environ.get("APPDATA", ""), r"Microsoft\Teams\blob_storage"),
            os.path.join(os.environ.get("APPDATA", ""), r"Microsoft\Teams\GPUCache"),
        ],
    },
    "discord_cache": {
        "label": "Discord Cache",
        "desc": "Discord app cache",
        "icon": "[MSG]",
        "paths": [
            os.path.join(os.environ.get("APPDATA", ""), r"discord\Cache"),
            os.path.join(os.environ.get("APPDATA", ""), r"discord\Code Cache"),
        ],
    },
    "thumbnail_cache": {
        "label": "Windows Thumbnail Cache",
        "desc": "Explorer thumbnail database",
        "icon": "[IMG]",
        "paths": [os.path.join(os.environ.get("LOCALAPPDATA", ""), r"Microsoft\Windows\Explorer")],
        "pattern": "thumbcache_*.db",
    },
    "update_cache": {
        "label": "Windows Update Cache",
        "desc": "Downloaded update files",
        "icon": "[UPD]",
        "paths": [r"C:\Windows\SoftwareDistribution\Download"],
        "admin_required": True,
    },
    "memory_dumps": {
        "label": "Memory Dump Files",
        "desc": "Crash dump files",
        "icon": "[MEM]",
        "paths": [r"C:\Windows\Minidump", r"C:\Windows\memory.dmp"],
        "admin_required": True,
    },
}

class ScanWorker(QThread):
    progress     = pyqtSignal(int, str)
    item_scanned = pyqtSignal(str, int)
    finished     = pyqtSignal(dict)

    def __init__(self, selected_keys):
        super().__init__()
        self.selected_keys = selected_keys

    def run(self):
        results = {}
        total = len(self.selected_keys)
        for i, key in enumerate(self.selected_keys):
            cfg = CLEANUP_TARGETS.get(key, {})
            self.progress.emit(int((i / total) * 100), f"Scanning {cfg.get('label', key)}…")
            size = 0
            if cfg.get("special") == "recycle_bin":
                size = self._scan_recycle_bin()
            elif "subfolders" in cfg:
                size = self._scan_subfolders(cfg)
            elif "pattern" in cfg:
                size = self._scan_patterns(cfg)
            else:
                for path in cfg.get("paths", []):
                    if path and os.path.exists(path):
                        size += get_folder_size(path)
            results[key] = size
            self.item_scanned.emit(key, size)
        self.progress.emit(100, "Scan complete")
        self.finished.emit(results)

    def _scan_recycle_bin(self):
        total = 0
        for drive in "CDEFGHIJKLMNOPQRSTUVWXYZ":
            rb = f"{drive}:\\$Recycle.Bin"
            if os.path.exists(rb):
                total += get_folder_size(rb)
        return total

    def _scan_subfolders(self, cfg):
        total = 0
        for base in cfg.get("paths", []):
            if not base or not os.path.exists(base):
                continue
            try:
                for profile in os.listdir(base):
                    pp = os.path.join(base, profile)
                    if os.path.isdir(pp):
                        for sub in cfg.get("subfolders", []):
                            sp = os.path.join(pp, sub)
                            if os.path.exists(sp):
                                total += get_folder_size(sp)
            except (OSError, PermissionError):
                pass
        return total

    def _scan_patterns(self, cfg):
        total = 0
        pat = cfg.get("pattern", "*")
        days = cfg.get("older_than_days")
        cutoff = time.time() - (days * 86400) if days else None
        for base in cfg.get("paths", []):
            if not base or not os.path.exists(base):
                continue
            for fp in glob.iglob(os.path.join(base, "**", pat), recursive=True):
                try:
                    if cutoff and os.path.getmtime(fp) > cutoff:
                        continue
                    total += os.path.getsize(fp)
                except (OSError, PermissionError):
                    pass
        return total

class CleanWorker(QThread):
    progress     = pyqtSignal(int, str)
    item_cleaned = pyqtSignal(str, int)
    finished     = pyqtSignal(int)

    def __init__(self, selected_keys):
        super().__init__()
        self.selected_keys = selected_keys

    def run(self):
        total_freed = 0
        count = len(self.selected_keys)
        for i, key in enumerate(self.selected_keys):
            cfg = CLEANUP_TARGETS.get(key, {})
            self.progress.emit(int((i / count) * 100), f"Cleaning {cfg.get('label', key)}…")
            freed = 0
            if cfg.get("special") == "recycle_bin":
                freed = self._clean_recycle_bin()
            elif "subfolders" in cfg:
                freed = self._clean_subfolders(cfg)
            elif "pattern" in cfg:
                freed = self._clean_patterns(cfg)
            else:
                for path in cfg.get("paths", []):
                    if path and os.path.exists(path):
                        freed += self._clean_folder_contents(path)
            total_freed += freed
            self.item_cleaned.emit(key, freed)
        self.progress.emit(100, "Cleaning complete")
        self.finished.emit(total_freed)

    def _clean_folder_contents(self, folder):
        freed = 0
        try:
            for item in os.listdir(folder):
                freed += safe_delete_path(os.path.join(folder, item))
        except (OSError, PermissionError):
            pass
        return freed

    def _clean_recycle_bin(self):
        freed = 0
        for drive in "CDEFGHIJKLMNOPQRSTUVWXYZ":
            rb = f"{drive}:\\$Recycle.Bin"
            if os.path.exists(rb):
                freed += self._clean_folder_contents(rb)
        return freed

    def _clean_subfolders(self, cfg):
        freed = 0
        for base in cfg.get("paths", []):
            if not base or not os.path.exists(base):
                continue
            try:
                for profile in os.listdir(base):
                    pp = os.path.join(base, profile)
                    if os.path.isdir(pp):
                        for sub in cfg.get("subfolders", []):
                            sp = os.path.join(pp, sub)
                            if os.path.exists(sp):
                                freed += self._clean_folder_contents(sp)
            except (OSError, PermissionError):
                pass
        return freed

    def _clean_patterns(self, cfg):
        freed = 0
        pat = cfg.get("pattern", "*")
        days = cfg.get("older_than_days")
        cutoff = time.time() - (days * 86400) if days else None
        for base in cfg.get("paths", []):
            if not base or not os.path.exists(base):
                continue
            for fp in glob.iglob(os.path.join(base, "**", pat), recursive=True):
                try:
                    if cutoff and os.path.getmtime(fp) > cutoff:
                        continue
                    freed += safe_delete_path(fp)
                except (OSError, PermissionError):
                    pass
        return freed

class TealButton(QPushButton):
    def __init__(self, text, parent=None, primary=True):
        super().__init__(text, parent)
        self.primary = primary
        self._setup()

    def _setup(self):
        if self.primary:
            self.setStyleSheet(f"""
                QPushButton {{ background: {TEAL}; color: #0D1117; border: none; border-radius: 8px;
                    padding: 10px 24px; font-size: 13px; font-weight: 700; }}
                QPushButton:hover {{ background: #00ECC0; }}
                QPushButton:pressed {{ background: {TEAL_DIM}; }}
                QPushButton:disabled {{ background: {TEXT_MUTED}; color: {BG_CARD}; }}
            """)
        else:
            self.setStyleSheet(f"""
                QPushButton {{ background: transparent; color: {TEAL}; border: 1.5px solid {TEAL};
                    border-radius: 8px; padding: 10px 24px; font-size: 13px; font-weight: 600; }}
                QPushButton:hover {{ background: {TEAL_GLOW}; border-color: #00ECC0; color: #00ECC0; }}
                QPushButton:pressed {{ background: {TEAL_DIM}22; }}
                QPushButton:disabled {{ border-color: {TEXT_MUTED}; color: {TEXT_MUTED}; }}
            """)
        self.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        self.setMinimumHeight(40)

class CleanupCard(QFrame):
    toggled = pyqtSignal(str, bool)

    def __init__(self, key, cfg, parent=None):
        super().__init__(parent)
        self.key = key
        self.scanned_size = 0
        self._setup_ui(cfg)

    def _setup_ui(self, cfg):
        self.setFixedHeight(72)
        self.setStyleSheet(f"""
            QFrame {{ background: {BG_CARD}; border: 1px solid {BORDER}; border-radius: 10px; }}
            QFrame:hover {{ border-color: {TEAL}55; background: {BG_HOVER}; }}
        """)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(16, 0, 16, 0)
        layout.setSpacing(12)

        icon_lbl = QLabel(cfg.get("icon", "[TMP]"))
        icon_lbl.setFixedSize(44, 22)
        icon_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        icon_lbl.setStyleSheet(
            f"color: {TEAL}; background: {TEAL}18; border: 1px solid {TEAL}44; border-radius: 4px;"
            f" font-size: 9px; font-weight: 700; font-family: 'Consolas', monospace;"
        )
        layout.addWidget(icon_lbl)

        text_layout = QVBoxLayout()
        text_layout.setSpacing(2)
        self.label_lbl = QLabel(cfg.get("label", self.key))
        self.label_lbl.setStyleSheet(f"color: {TEXT_MAIN}; font-size: 13px; font-weight: 600; background: transparent; border: none;")
        self.desc_lbl = QLabel(cfg.get("desc", ""))
        self.desc_lbl.setStyleSheet(f"color: {TEXT_SUB}; font-size: 11px; background: transparent; border: none;")

        if cfg.get("admin_required"):
            badge = QLabel("  Admin  ")
            badge.setStyleSheet(f"color: {AMBER}; background: {AMBER}22; border: 1px solid {AMBER}44; border-radius: 4px; font-size: 9px; font-weight: 700; padding: 1px 4px;")
            row = QHBoxLayout()
            row.setSpacing(6)
            row.addWidget(self.label_lbl)
            row.addWidget(badge)
            row.addStretch()
            w = QWidget()
            w.setStyleSheet("background: transparent; border: none;")
            w.setLayout(row)
            text_layout.addWidget(w)
        else:
            text_layout.addWidget(self.label_lbl)
        text_layout.addWidget(self.desc_lbl)
        layout.addLayout(text_layout)
        layout.addStretch()

        self.size_lbl = QLabel("—")
        self.size_lbl.setFixedWidth(80)
        self.size_lbl.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self.size_lbl.setStyleSheet(f"color: {TEXT_SUB}; font-size: 12px; font-weight: 500; background: transparent; border: none;")
        layout.addWidget(self.size_lbl)

        self.checkbox = QCheckBox()
        self.checkbox.setChecked(True)
        self.checkbox.setStyleSheet(f"""
            QCheckBox::indicator {{ width: 20px; height: 20px; border-radius: 6px; border: 2px solid {BORDER}; background: {BG_DARK}; }}
            QCheckBox::indicator:hover {{ border-color: {TEAL}; }}
            QCheckBox::indicator:checked {{ background: {TEAL}; border-color: {TEAL}; }}
        """)
        self.checkbox.stateChanged.connect(lambda s: self.toggled.emit(self.key, bool(s)))
        layout.addWidget(self.checkbox)

    def set_size(self, size):
        self.scanned_size = size
        if size == 0:
            self.size_lbl.setText("—")
            self.size_lbl.setStyleSheet(f"color: {TEXT_MUTED}; font-size: 12px; background: transparent; border: none;")
        else:
            self.size_lbl.setText(format_bytes(size))
            color = RED_WARN if size > 500*1024*1024 else TEAL if size > 10*1024*1024 else TEXT_SUB
            self.size_lbl.setStyleSheet(f"color: {color}; font-size: 12px; font-weight: 600; background: transparent; border: none;")

    def mark_cleaned(self):
        self.size_lbl.setText("✓ Cleaned")
        self.size_lbl.setStyleSheet(f"color: {TEAL}; font-size: 11px; font-weight: 700; background: transparent; border: none;")

class GlowProgressBar(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedHeight(6)
        self._value = 0

    @pyqtProperty(int)
    def value(self):
        return self._value

    @value.setter
    def value(self, v):
        self._value = max(0, min(100, v))
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        w, h = self.width(), self.height()
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(BORDER))
        painter.drawRoundedRect(0, 0, w, h, h//2, h//2)
        if self._value > 0:
            fill_w = int(w * self._value / 100)
            grad = QLinearGradient(0, 0, fill_w, 0)
            grad.setColorAt(0, QColor(TEAL_DIM))
            grad.setColorAt(1, QColor(TEAL))
            painter.setBrush(QBrush(grad))
            painter.drawRoundedRect(0, 0, fill_w, h, h//2, h//2)
        painter.end()

class StatBadge(QFrame):
    def __init__(self, label, value="—", parent=None):
        super().__init__(parent)
        self.setStyleSheet(f"QFrame {{ background: {BG_CARD}; border: 1px solid {BORDER}; border-radius: 10px; }}")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 12, 16, 12)
        layout.setSpacing(4)
        self.val_lbl = QLabel(value)
        self.val_lbl.setStyleSheet(f"color: {TEAL}; font-size: 22px; font-weight: 700; background: transparent; border: none;")
        self.val_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.key_lbl = QLabel(label)
        self.key_lbl.setStyleSheet(f"color: {TEXT_SUB}; font-size: 10px; font-weight: 500; letter-spacing: 0.8px; background: transparent; border: none;")
        self.key_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self.val_lbl)
        layout.addWidget(self.key_lbl)

    def set_value(self, v):
        self.val_lbl.setText(v)

class SweptPC(QMainWindow):
    def __init__(self):
        super().__init__()
        self.selected_keys = set(CLEANUP_TARGETS.keys())
        self.scan_results = {}
        self.cards = {}
        self._scan_worker = None
        self._clean_worker = None
        self._setup_window()
        self._build_ui()

    def _setup_window(self):
        self.setWindowTitle(f"{APP_NAME}  ·  by {BRAND}")
        self.setMinimumSize(720, 760)
        self.resize(760, 840)
        self.setStyleSheet(f"""
            QMainWindow, QWidget {{ background: {BG_DARK}; color: {TEXT_MAIN}; font-family: 'Segoe UI', sans-serif; }}
            QScrollArea {{ background: transparent; border: none; }}
            QScrollBar:vertical {{ background: {BG_DARK}; width: 6px; border-radius: 3px; }}
            QScrollBar::handle:vertical {{ background: {BORDER}; border-radius: 3px; min-height: 30px; }}
            QScrollBar::handle:vertical:hover {{ background: {TEXT_MUTED}; }}
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0; }}
        """)
        screen = QApplication.primaryScreen().availableGeometry()
        self.move((screen.width() - self.width()) // 2, (screen.height() - self.height()) // 2)

    def _build_ui(self):
        central = QWidget()
        self.setCentralWidget(central)
        root = QVBoxLayout(central)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        header = QWidget()
        header.setFixedHeight(72)
        header.setStyleSheet(f"background: {BG_CARD}; border-bottom: 1px solid {BORDER};")
        h_layout = QHBoxLayout(header)
        h_layout.setContentsMargins(24, 0, 24, 0)
        logo_dot = QLabel("●")
        logo_dot.setStyleSheet(f"color: {TEAL}; font-size: 10px; background: transparent;")
        app_name = QLabel(APP_NAME)
        app_name.setStyleSheet(f"color: {TEXT_MAIN}; font-size: 18px; font-weight: 700; background: transparent;")
        brand_lbl = QLabel(f"by {BRAND}")
        brand_lbl.setStyleSheet(f"color: {TEXT_MUTED}; font-size: 11px; background: transparent; margin-left: 8px;")
        ver_lbl = QLabel(f"v{APP_VERSION}")
        ver_lbl.setStyleSheet(f"color: {TEAL}; background: {TEAL}18; border: 1px solid {TEAL}44; border-radius: 4px; font-size: 10px; font-weight: 600; padding: 2px 6px;")
        h_layout.addWidget(logo_dot)
        h_layout.addSpacing(6)
        h_layout.addWidget(app_name)
        h_layout.addWidget(brand_lbl)
        h_layout.addSpacing(8)
        h_layout.addWidget(ver_lbl)
        h_layout.addStretch()
        companion = QLabel("companion to PulseMonitor")
        companion.setStyleSheet(f"color: {TEXT_MUTED}; font-size: 10px; background: transparent;")
        h_layout.addWidget(companion)
        root.addWidget(header)

        body = QWidget()
        body_layout = QVBoxLayout(body)
        body_layout.setContentsMargins(24, 20, 24, 20)
        body_layout.setSpacing(16)

        stats_row = QHBoxLayout()
        stats_row.setSpacing(12)
        self.stat_found   = StatBadge("SPACE FOUND", "—")
        self.stat_freed   = StatBadge("SPACE FREED", "—")
        self.stat_items   = StatBadge("ITEMS SELECTED", "0")
        self.stat_cleaned = StatBadge("LAST CLEANED", "Never")
        for s in (self.stat_found, self.stat_freed, self.stat_items, self.stat_cleaned):
            stats_row.addWidget(s)
        body_layout.addLayout(stats_row)

        row = QHBoxLayout()
        sec_lbl = QLabel("CLEANUP TARGETS")
        sec_lbl.setStyleSheet(f"color: {TEXT_MUTED}; font-size: 10px; font-weight: 700; letter-spacing: 1.2px; background: transparent;")
        self.select_all_btn = QPushButton("Deselect All")
        self.select_all_btn.setStyleSheet(f"QPushButton {{ background: transparent; color: {TEXT_SUB}; border: none; font-size: 11px; padding: 0; }} QPushButton:hover {{ color: {TEAL}; }}")
        self.select_all_btn.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        self.select_all_btn.clicked.connect(self._toggle_all)
        self._all_selected = True
        row.addWidget(sec_lbl)
        row.addStretch()
        row.addWidget(self.select_all_btn)
        body_layout.addLayout(row)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        cards_widget = QWidget()
        cards_widget.setStyleSheet("background: transparent;")
        cards_layout = QVBoxLayout(cards_widget)
        cards_layout.setContentsMargins(0, 0, 4, 0)
        cards_layout.setSpacing(6)
        for key, cfg in CLEANUP_TARGETS.items():
            card = CleanupCard(key, cfg)
            card.toggled.connect(self._on_card_toggled)
            self.cards[key] = card
            cards_layout.addWidget(card)
        cards_layout.addStretch()
        scroll.setWidget(cards_widget)
        body_layout.addWidget(scroll, stretch=1)

        self.progress_label = QLabel("")
        self.progress_label.setStyleSheet(f"color: {TEXT_SUB}; font-size: 11px; background: transparent;")
        self.progress_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.progress_bar = GlowProgressBar()
        self.progress_bar.setVisible(False)
        body_layout.addWidget(self.progress_label)
        body_layout.addWidget(self.progress_bar)

        btn_row = QHBoxLayout()
        btn_row.setSpacing(10)
        self.scan_btn  = TealButton("🔍  Scan", primary=False)
        self.clean_btn = TealButton("🧹  Clean Selected", primary=True)
        self.clean_btn.setEnabled(False)
        self.scan_btn.clicked.connect(self._start_scan)
        self.clean_btn.clicked.connect(self._start_clean)
        btn_row.addWidget(self.scan_btn)
        btn_row.addWidget(self.clean_btn)
        body_layout.addLayout(btn_row)

        footer = QLabel(f"{APP_NAME} {APP_VERSION}  ·  by {BRAND}  ·  Free &amp; Portable")
        footer.setAlignment(Qt.AlignmentFlag.AlignCenter)
        footer.setStyleSheet(f"color: {TEXT_MUTED}; font-size: 10px; background: transparent;")
        body_layout.addWidget(footer)
        root.addWidget(body, stretch=1)
        self._update_stats()

    def _toggle_all(self):
        self._all_selected = not self._all_selected
        for key, card in self.cards.items():
            card.checkbox.blockSignals(True)
            card.checkbox.setChecked(self._all_selected)
            card.checkbox.blockSignals(False)
            if self._all_selected:
                self.selected_keys.add(key)
            else:
                self.selected_keys.discard(key)
        self.select_all_btn.setText("Deselect All" if self._all_selected else "Select All")
        self._update_stats()

    def _on_card_toggled(self, key, checked):
        if checked:
            self.selected_keys.add(key)
        else:
            self.selected_keys.discard(key)
        self._update_stats()

    def _update_stats(self):
        self.stat_items.set_value(str(len(self.selected_keys)))
        if self.scan_results:
            total = sum(v for k, v in self.scan_results.items() if k in self.selected_keys)
            self.stat_found.set_value(format_bytes(total))

    def _start_scan(self):
        if not self.selected_keys:
            return
        self.scan_btn.setEnabled(False)
        self.clean_btn.setEnabled(False)
        self.progress_bar.setVisible(True)
        self.progress_bar.value = 0
        for card in self.cards.values():
            card.size_lbl.setText("…")
            card.size_lbl.setStyleSheet(f"color: {TEXT_MUTED}; font-size: 12px; background: transparent; border: none;")
        self._scan_worker = ScanWorker(list(self.selected_keys))
        self._scan_worker.progress.connect(self._on_scan_progress)
        self._scan_worker.item_scanned.connect(self._on_item_scanned)
        self._scan_worker.finished.connect(self._on_scan_finished)
        self._scan_worker.start()

    def _on_scan_progress(self, pct, msg):
        self.progress_bar.value = pct
        self.progress_label.setText(msg)

    def _on_item_scanned(self, key, size):
        self.scan_results[key] = size
        if key in self.cards:
            self.cards[key].set_size(size)
        self._update_stats()

    def _on_scan_finished(self, results):
        self.scan_results = results
        total = sum(results.values())
        self.stat_found.set_value(format_bytes(total))
        self.progress_label.setText(f"Scan complete — found {format_bytes(total)} across {len(results)} categories")
        self.progress_bar.setVisible(False)
        self.scan_btn.setEnabled(True)
        self.clean_btn.setEnabled(bool(self.selected_keys))
        self._update_stats()

    def _start_clean(self):
        if not self.selected_keys:
            return
        self.scan_btn.setEnabled(False)
        self.clean_btn.setEnabled(False)
        self.progress_bar.setVisible(True)
        self.progress_bar.value = 0
        self._clean_worker = CleanWorker(list(self.selected_keys))
        self._clean_worker.progress.connect(self._on_clean_progress)
        self._clean_worker.item_cleaned.connect(self._on_item_cleaned)
        self._clean_worker.finished.connect(self._on_clean_finished)
        self._clean_worker.start()

    def _on_clean_progress(self, pct, msg):
        self.progress_bar.value = pct
        self.progress_label.setText(msg)

    def _on_item_cleaned(self, key, freed):
        if key in self.cards:
            self.cards[key].mark_cleaned()
        if key in self.scan_results:
            self.scan_results[key] = 0

    def _on_clean_finished(self, total_freed):
        total_freed = max(0, total_freed)
        self.stat_freed.set_value(format_bytes(total_freed))
        self.stat_cleaned.set_value(datetime.now().strftime("%H:%M"))
        self.stat_found.set_value("0 B")
        self.progress_label.setText(f"✓  Freed {format_bytes(total_freed)} — your PC is cleaner!")
        self.progress_label.setStyleSheet(f"color: {TEAL}; font-size: 11px; background: transparent;")
        self.progress_bar.setVisible(False)
        self.scan_btn.setEnabled(True)
        self.clean_btn.setEnabled(False)

def main():
    app = QApplication(sys.argv)
    app.setApplicationName(APP_NAME)
    app.setApplicationVersion(APP_VERSION)
    app.setOrganizationName(BRAND)
    for p in [os.path.join(os.path.dirname(__file__), "icon.png"),
              os.path.join(os.path.dirname(__file__), "icon.ico"),
              r"C:\Users\Josh\Desktop\Screenshot 2026-04-17 224518.png"]:
        if os.path.exists(p):
            app.setWindowIcon(QIcon(p))
            break
    palette = QPalette()
    palette.setColor(QPalette.ColorRole.Window,        QColor(BG_DARK))
    palette.setColor(QPalette.ColorRole.WindowText,    QColor(TEXT_MAIN))
    palette.setColor(QPalette.ColorRole.Base,          QColor(BG_CARD))
    palette.setColor(QPalette.ColorRole.Text,          QColor(TEXT_MAIN))
    palette.setColor(QPalette.ColorRole.Button,        QColor(BG_CARD))
    palette.setColor(QPalette.ColorRole.Highlight,     QColor(TEAL))
    palette.setColor(QPalette.ColorRole.HighlightedText, QColor(BG_DARK))
    app.setPalette(palette)
    window = SweptPC()
    window.show()
    sys.exit(app.exec())

if __name__ == "__main__":
    main()
