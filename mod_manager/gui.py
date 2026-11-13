#!/usr/bin/env python3
"""PyQt6 Graphical User Interface for Hookshot Mod Manager."""

import os
import subprocess
import sys
from typing import List, Optional

from PyQt6.QtCore import QPoint, Qt, QTimer
from PyQt6.QtGui import QAction, QColor, QFont, QIcon
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QCheckBox,
    QDialog,
    QFileDialog,
    QFormLayout,
    QFrame,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMenu,
    QMessageBox,
    QPushButton,
    QSplitter,
    QStatusBar,
    QStyle,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

try:
    from .core import (GameDirNotFound, HookshotModManager, child_env, dev_overlay_lib, is_game_dir, load_config,
                       runtime_title, save_config)
    from .models import Mod
except (ImportError, ValueError):
    from core import (GameDirNotFound, HookshotModManager, child_env, dev_overlay_lib, is_game_dir, load_config,
                      runtime_title, save_config)
    from models import Mod

DARK_STYLE = """
QMainWindow, QDialog {
    background-color: #1a1c23;
    color: #e2e8f0;
}
QWidget {
    font-family: 'Segoe UI', 'Ubuntu', 'Helvetica Neue', sans-serif;
    font-size: 13px;
    color: #e2e8f0;
}
QFrame#headerFrame {
    background-color: #222631;
    border-bottom: 2px solid #2d3342;
    padding: 12px;
}
QTableWidget {
    background-color: #1f232d;
    alternate-background-color: #242935;
    border: 1px solid #2d3342;
    border-radius: 6px;
    gridline-color: #2b303e;
    selection-background-color: #315243;
    selection-color: #ffffff;
}
QHeaderView::section {
    background-color: #282d3a;
    color: #94a3b8;
    padding: 6px;
    border: none;
    border-right: 1px solid #333a4a;
    font-weight: bold;
}
QPushButton {
    background-color: #2d3342;
    color: #f1f5f9;
    border: 1px solid #3b4254;
    border-radius: 5px;
    padding: 7px 14px;
    font-weight: 500;
}
QPushButton:hover {
    background-color: #3b4254;
    border-color: #4b5568;
}
QPushButton:pressed {
    background-color: #222631;
}
QPushButton#playBtn {
    background-color: #10b981;
    color: #ffffff;
    border: 1px solid #059669;
    font-weight: bold;
    font-size: 14px;
    padding: 8px 20px;
}
QPushButton#playBtn:hover {
    background-color: #059669;
}
QPushButton#actionBtn {
    background-color: #2563eb;
    color: #ffffff;
    border: 1px solid #1d4ed8;
    font-weight: bold;
}
QPushButton#actionBtn:hover {
    background-color: #1d4ed8;
}
QPushButton#dangerBtn {
    background-color: #dc2626;
    color: #ffffff;
    border: 1px solid #b91c1c;
}
QPushButton#dangerBtn:hover {
    background-color: #b91c1c;
}
QLineEdit, QTextEdit, QListWidget {
    background-color: #1f232d;
    border: 1px solid #333a4a;
    border-radius: 5px;
    padding: 6px;
    color: #f8fafc;
}
QLineEdit:focus, QTextEdit:focus, QListWidget:focus {
    border: 1px solid #10b981;
}
QTabWidget::pane {
    border: 1px solid #2d3342;
    background-color: #1f232d;
    border-radius: 6px;
}
QTabBar::tab {
    background-color: #222631;
    color: #94a3b8;
    padding: 8px 16px;
    border-top-left-radius: 5px;
    border-top-right-radius: 5px;
    margin-right: 2px;
}
QTabBar::tab:selected {
    background-color: #1f232d;
    color: #10b981;
    border-bottom: 2px solid #10b981;
    font-weight: bold;
}
QScrollBar:vertical {
    background: #1a1c23;
    width: 10px;
    margin: 0px;
}
QScrollBar::handle:vertical {
    background: #3b4254;
    min-height: 20px;
    border-radius: 5px;
}
QScrollBar::handle:vertical:hover {
    background: #4b5568;
}
QStatusBar {
    background-color: #1a1c23;
    color: #64748b;
    border-top: 1px solid #2d3342;
}
"""


class CreateModDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Create New Mod Template")
        self.resize(450, 300)

        layout = QVBoxLayout(self)

        form = QFormLayout()
        self.name_edit = QLineEdit()
        self.name_edit.setPlaceholderText("e.g. KokiriSwordDamageBuff")
        self.author_edit = QLineEdit("Justin")
        self.version_edit = QLineEdit("1.0.0")
        self.category_edit = QLineEdit("Gameplay")
        self.desc_edit = QTextEdit()
        self.desc_edit.setPlaceholderText("Describe what this mod does...")
        self.desc_edit.setMaximumHeight(80)

        form.addRow("Mod Name *:", self.name_edit)
        form.addRow("Author:", self.author_edit)
        form.addRow("Version:", self.version_edit)
        form.addRow("Category:", self.category_edit)
        form.addRow("Description:", self.desc_edit)

        layout.addLayout(form)

        btn_layout = QHBoxLayout()
        self.cancel_btn = QPushButton("Cancel")
        self.cancel_btn.clicked.connect(self.reject)
        self.create_btn = QPushButton("Create Mod")
        self.create_btn.setObjectName("actionBtn")
        self.create_btn.clicked.connect(self.accept)
        btn_layout.addStretch()
        btn_layout.addWidget(self.cancel_btn)
        btn_layout.addWidget(self.create_btn)

        layout.addLayout(btn_layout)


class MainWindow(QMainWindow):
    def __init__(self, game_dir: Optional[str] = None):
        super().__init__()
        while True:                                  # find the game folder, or ask for it (remembered afterwards)
            try:
                self.mgr = HookshotModManager(game_dir)
                break
            except GameDirNotFound as e:
                QMessageBox.information(self, "Hookshot Mod Manager",
                                        f"{e}\n\nPick the game folder: the one that holds hookshot.exe or longshot.exe (Windows), "
                                        "or bin/ with the hookshot or longshot launcher (Linux).")
                game_dir = QFileDialog.getExistingDirectory(self, "Hookshot or Longshot game folder")
                if not game_dir:
                    raise SystemExit(1)
        self.current_selected_mod: Optional[Mod] = None

        self.setWindowTitle("Hookshot Mod Manager — The Legend of Zelda: Ocarina of Time Remastered")
        self.resize(1100, 720)
        self.setStyleSheet(DARK_STYLE)

        # the app's own icon: bundled in the packaged app (sys._MEIPASS) or in the source checkout's packaging/
        base = getattr(sys, "_MEIPASS", os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        icon_path = os.path.join(base, "packaging", "icon.png")
        if os.path.exists(icon_path):
            self.setWindowIcon(QIcon(icon_path))

        self.init_ui()
        self.refresh_mods()
        QTimer.singleShot(400, self.offer_included)      # (after the window shows)

    def init_ui(self):
        central = QWidget()
        self.setCentralWidget(central)
        main_layout = QVBoxLayout(central)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(0)

        # Header Frame
        header = QFrame()
        header.setObjectName("headerFrame")
        h_layout = QHBoxLayout(header)
        h_layout.setContentsMargins(16, 12, 16, 12)

        title_vbox = QVBoxLayout()
        title_vbox.setSpacing(3)
        title_lbl = QLabel("HOOKSHOT MOD MANAGER")
        title_lbl.setFont(QFont("Arial", 16, QFont.Weight.Bold))
        title_lbl.setStyleSheet("color: #10b981; letter-spacing: 1px;")

        sub_lbl = QLabel("The Legend of Zelda: Ocarina of Time Remastered (Switch 2 Recompilation)")
        sub_lbl.setStyleSheet("color: #94a3b8; font-size: 12px;")

        title_vbox.addWidget(title_lbl)
        title_vbox.addWidget(sub_lbl)

        # the game folder in use, with a button to pick another (saved for next time)
        folder_row = QHBoxLayout()
        folder_row.setSpacing(8)
        self.folder_lbl = QLabel()
        self.folder_lbl.setStyleSheet("color: #64748b; font-size: 11px;")
        self.folder_lbl.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.folder_btn = QPushButton("Change...")
        self.folder_btn.setToolTip("Use another Hookshot or Longshot game folder")
        self.folder_btn.setStyleSheet("font-size: 11px; padding: 2px 10px;")
        self.folder_btn.clicked.connect(self.on_change_game_dir)
        folder_row.addWidget(self.folder_lbl)
        folder_row.addWidget(self.folder_btn)
        folder_row.addStretch()
        title_vbox.addLayout(folder_row)
        self.show_game_dir()

        h_layout.addLayout(title_vbox)
        h_layout.addStretch()

        play_vbox = QVBoxLayout()
        play_vbox.setSpacing(4)
        play_vbox.setAlignment(Qt.AlignmentFlag.AlignRight)

        self.play_btn = QPushButton("▶  LAUNCH GAME")
        self.play_btn.setObjectName("playBtn")
        self.play_btn.clicked.connect(self.on_launch_game)
        play_vbox.addWidget(self.play_btn)

        self.overlay_checkbox = QCheckBox("Enable F2 Dev Overlay")
        self.overlay_checkbox.setChecked(True)
        self.overlay_checkbox.setToolTip("Injects the in-game Dev & Mod Playground overlay accessible via [F2]")
        self.overlay_checkbox.setStyleSheet("color: #10b981; font-weight: bold; font-size: 11px;")
        # (the developer overlay is Linux-only and exists only in a development folder)
        self.overlay_checkbox.setVisible(not sys.platform.startswith("win") and
                                         dev_overlay_lib() is not None and self.mgr.runtime == "hookshot")
        play_vbox.addWidget(self.overlay_checkbox)

        h_layout.addLayout(play_vbox)

        main_layout.addWidget(header)

        # Toolbar
        toolbar = QWidget()
        t_layout = QHBoxLayout(toolbar)
        t_layout.setContentsMargins(16, 10, 16, 10)
        t_layout.setSpacing(8)

        self.install_btn = QPushButton("Install Mod Archive...")
        self.install_btn.setObjectName("actionBtn")
        self.install_btn.clicked.connect(self.on_install_mod)

        self.create_btn = QPushButton("+ New Mod Template")
        self.create_btn.clicked.connect(self.on_create_mod)

        # the mods that come with Hookshot Mods (only shown when the app carries some)
        self.included_btn = QPushButton("Included Mods...")
        self.included_btn.setToolTip("Mods that come with Hookshot Mods: install or update them")
        self.included_btn.clicked.connect(lambda: self.offer_included(always=True))
        self.included_btn.setVisible(bool(self.mgr.bundled_status()))

        self.enable_all_btn = QPushButton("Enable All")
        self.enable_all_btn.clicked.connect(self.on_enable_all)

        self.disable_all_btn = QPushButton("Disable All")
        self.disable_all_btn.clicked.connect(self.on_disable_all)

        self.move_up_btn = QPushButton("▲ Priority Up")
        self.move_up_btn.clicked.connect(self.on_move_up)

        self.move_down_btn = QPushButton("▼ Priority Down")
        self.move_down_btn.clicked.connect(self.on_move_down)

        self.open_folder_btn = QPushButton("Open Mods Folder")
        self.open_folder_btn.clicked.connect(self.on_open_mods_folder)

        self.delete_btn = QPushButton("Delete Mod")
        self.delete_btn.setObjectName("dangerBtn")
        self.delete_btn.clicked.connect(self.on_delete_mod)

        t_layout.addWidget(self.install_btn)
        t_layout.addWidget(self.included_btn)
        t_layout.addWidget(self.create_btn)
        t_layout.addWidget(self.enable_all_btn)
        t_layout.addWidget(self.disable_all_btn)
        t_layout.addWidget(self.move_up_btn)
        t_layout.addWidget(self.move_down_btn)
        t_layout.addStretch()
        t_layout.addWidget(self.open_folder_btn)
        t_layout.addWidget(self.delete_btn)

        main_layout.addWidget(toolbar)

        # Main Splitter: Left = Table, Right = Details/Tabs
        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.setContentsMargins(16, 0, 16, 16)

        # Left Panel: Table
        table_container = QWidget()
        table_layout = QVBoxLayout(table_container)
        table_layout.setContentsMargins(0, 0, 8, 0)

        self.mod_table = QTableWidget()
        self.mod_table.setColumnCount(6)
        self.mod_table.setHorizontalHeaderLabels(["", "Order", "Mod Name", "Ver", "Files", "Conflicts"])
        self.mod_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.mod_table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.mod_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.mod_table.verticalHeader().setVisible(False)
        self.mod_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Fixed)
        self.mod_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Fixed)
        self.mod_table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        self.mod_table.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeMode.Fixed)
        self.mod_table.horizontalHeader().setSectionResizeMode(4, QHeaderView.ResizeMode.Fixed)
        self.mod_table.horizontalHeader().setSectionResizeMode(5, QHeaderView.ResizeMode.Fixed)

        self.mod_table.setColumnWidth(0, 36)
        self.mod_table.setColumnWidth(1, 60)
        self.mod_table.setColumnWidth(3, 70)
        self.mod_table.setColumnWidth(4, 65)
        self.mod_table.setColumnWidth(5, 85)

        self.mod_table.itemSelectionChanged.connect(self.on_table_selection_changed)
        self.mod_table.cellChanged.connect(self.on_cell_changed)
        self.mod_table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.mod_table.customContextMenuRequested.connect(self.on_table_context_menu)

        table_layout.addWidget(self.mod_table)
        splitter.addWidget(table_container)

        # Right Panel: Tabs
        self.tabs = QTabWidget()

        # Tab 1: Mod Details
        details_tab = QWidget()
        d_layout = QVBoxLayout(details_tab)
        d_layout.setContentsMargins(12, 12, 12, 12)

        self.detail_title = QLabel("Select a mod to inspect")
        self.detail_title.setFont(QFont("Arial", 14, QFont.Weight.Bold))
        self.detail_title.setStyleSheet("color: #10b981;")

        self.detail_meta = QLabel("")
        self.detail_meta.setStyleSheet("color: #94a3b8; font-size: 12px;")

        self.detail_desc = QLabel("")
        self.detail_desc.setWordWrap(True)
        self.detail_desc.setStyleSheet("color: #cbd5e1; margin: 6px 0px;")

        # File List inside Mod
        files_box = QGroupBox("RomFS Content Files")
        fb_layout = QVBoxLayout(files_box)
        self.file_search = QLineEdit()
        self.file_search.setPlaceholderText("Filter files...")
        self.file_search.textChanged.connect(self.on_file_search_filter)

        self.mod_files_list = QListWidget()
        fb_layout.addWidget(self.file_search)
        fb_layout.addWidget(self.mod_files_list)

        d_layout.addWidget(self.detail_title)
        d_layout.addWidget(self.detail_meta)
        d_layout.addWidget(self.detail_desc)
        d_layout.addWidget(files_box)

        self.tabs.addTab(details_tab, "Mod Details")

        # Tab 2: Conflicts Viewer
        conflicts_tab = QWidget()
        c_layout = QVBoxLayout(conflicts_tab)
        c_layout.setContentsMargins(12, 12, 12, 12)

        c_info = QLabel("File Overlaps & Precedence (Later loads override earlier ones):")
        c_info.setStyleSheet("color: #94a3b8; font-weight: bold;")
        self.conflict_table = QTableWidget()
        self.conflict_table.setColumnCount(3)
        self.conflict_table.setHorizontalHeaderLabels(["RomFS File", "Winning Mod", "Load Chain"])
        self.conflict_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.conflict_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        self.conflict_table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        self.conflict_table.verticalHeader().setVisible(False)
        self.conflict_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)

        c_layout.addWidget(c_info)
        c_layout.addWidget(self.conflict_table)

        self.tabs.addTab(conflicts_tab, "Conflicts (0)")

        # Tab 3: Asset Extractor
        dump_tab = QWidget()
        dump_layout = QVBoxLayout(dump_tab)
        dump_layout.setContentsMargins(12, 12, 12, 12)

        dump_info = QLabel("Extract & Decompress Base Game Assets:")
        dump_info.setStyleSheet("color: #94a3b8; font-weight: bold;")

        search_row = QHBoxLayout()
        self.romfs_search_input = QLineEdit()
        self.romfs_search_input.setPlaceholderText("Search romfs (e.g. Kokiri, MasterSword, Navi, ActorInfo)...")
        self.romfs_search_input.returnPressed.connect(self.on_search_romfs)
        self.romfs_search_btn = QPushButton("Search")
        self.romfs_search_btn.clicked.connect(self.on_search_romfs)
        search_row.addWidget(self.romfs_search_input)
        search_row.addWidget(self.romfs_search_btn)

        self.romfs_results_list = QListWidget()

        action_row = QHBoxLayout()
        self.extract_to_mod_btn = QPushButton("Extract into Selected Mod")
        self.extract_to_mod_btn.setObjectName("actionBtn")
        self.extract_to_mod_btn.clicked.connect(self.on_extract_to_mod)

        self.extract_to_file_btn = QPushButton("Export to Folder...")
        self.extract_to_file_btn.clicked.connect(self.on_extract_to_file)

        action_row.addStretch()
        action_row.addWidget(self.extract_to_mod_btn)
        action_row.addWidget(self.extract_to_file_btn)

        dump_layout.addWidget(dump_info)
        dump_layout.addLayout(search_row)
        dump_layout.addWidget(self.romfs_results_list)
        dump_layout.addLayout(action_row)

        self.tabs.addTab(dump_tab, "Asset Extractor")

        splitter.addWidget(self.tabs)
        splitter.setSizes([550, 450])
        main_layout.addWidget(splitter)

        # Status Bar
        self.status = QStatusBar()
        self.setStatusBar(self.status)
        self.status.showMessage(f"Ready. Game Directory: {self.mgr.game_dir}")

    def show_game_dir(self):
        d = self.mgr.game_dir
        self.folder_lbl.setText(f"{runtime_title(self.mgr.runtime)} game folder: " + (d if len(d) <= 70 else "…" + d[-69:]))
        self.folder_lbl.setToolTip(d)
        if hasattr(self, "overlay_checkbox"):   # (the developer overlay is built against Hookshot)
            self.overlay_checkbox.setVisible(not sys.platform.startswith("win") and dev_overlay_lib() is not None and
                                             self.mgr.runtime == "hookshot")

    def on_change_game_dir(self):
        """Picks another game folder: checked, saved for next time, and the window switches to its mods."""
        start = os.path.dirname(self.mgr.game_dir)
        while True:
            path = QFileDialog.getExistingDirectory(self, "Hookshot or Longshot game folder", start)
            if not path:
                return
            path = os.path.abspath(path)
            if is_game_dir(path):
                break
            QMessageBox.warning(self, "Game Folder",
                                f"{path}\n\nThis is not a Hookshot or Longshot game folder. Pick the folder that holds "
                                "hookshot.exe or longshot.exe (Windows), or bin/ with the hookshot or longshot launcher (Linux).")
            start = path
        if path == self.mgr.game_dir:
            return
        try:
            mgr = HookshotModManager(path)
        except Exception as e:
            QMessageBox.critical(self, "Game Folder", f"Could not use {path}:\n{e}")
            return
        self.mgr = mgr
        self.current_selected_mod = None
        self.detail_title.setText("")
        self.detail_meta.setText("")
        self.detail_desc.setText("")
        self.mod_files_list.clear()
        self.romfs_results_list.clear()
        self.included_btn.setVisible(bool(self.mgr.bundled_status()))
        self.show_game_dir()
        self.refresh_mods()
        self.status.showMessage(f"{runtime_title(self.mgr.runtime)} game folder: {self.mgr.game_dir}")
        env = os.environ.get("HOOKSHOT_GAME_DIR")
        if env and os.path.abspath(env) != self.mgr.game_dir:
            QMessageBox.information(self, "Game Folder",
                                    f"HOOKSHOT_GAME_DIR is set to {env}: it is used instead of the saved folder when "
                                    "Hookshot Mods starts.")
        self.offer_included()

    def refresh_mods(self):
        self.mod_table.blockSignals(True)
        self.mod_table.setRowCount(0)

        mods = self.mgr.list_mods()
        conflicts = self.mgr.get_conflicts()

        self.mod_table.setRowCount(len(mods))
        for row, mod in enumerate(mods):
            # Checkbox for enabled/disabled
            chk_item = QTableWidgetItem()
            chk_item.setFlags(Qt.ItemFlag.ItemIsUserCheckable | Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable)
            chk_item.setCheckState(Qt.CheckState.Checked if mod.enabled else Qt.CheckState.Unchecked)
            self.mod_table.setItem(row, 0, chk_item)

            # Order
            order_item = QTableWidgetItem(f"{row + 1}")
            order_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            self.mod_table.setItem(row, 1, order_item)

            # Mod Name (code mods: a gear, and their layer's state as the tooltip)
            name_item = QTableWidgetItem(("⚙ " if mod.is_code_mod else "") + mod.display_name)
            if not mod.enabled:
                name_item.setForeground(QColor("#64748b"))
            elif mod.is_code_mod and mod.code_layer.problem:
                name_item.setForeground(QColor("#f59e0b"))
            else:
                name_item.setForeground(QColor("#f8fafc"))
            if mod.is_code_mod:
                lay = mod.code_layer
                name_item.setToolTip(f"Code mod (Vulkan layer {lay.name})\n" +
                                     (f"Cannot load on this PC: {lay.problem}" if lay.problem else
                                      "Loads when you start the game from Hookshot Mods (Launch)"))
            name_item.setData(Qt.ItemDataRole.UserRole, mod.folder_name)
            self.mod_table.setItem(row, 2, name_item)

            # Version
            ver_item = QTableWidgetItem(mod.metadata.version or "1.0")
            ver_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            ver_item.setForeground(QColor("#94a3b8"))
            self.mod_table.setItem(row, 3, ver_item)

            # File count
            cnt_item = QTableWidgetItem(str(mod.file_count) + (" + code" if mod.is_code_mod else ""))
            cnt_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            self.mod_table.setItem(row, 4, cnt_item)

            # Conflicts
            mod_conflicts = [c for c in conflicts.values() if mod.folder_name in c.mods]
            if mod_conflicts and mod.enabled:
                c_item = QTableWidgetItem(f"⚠️ {len(mod_conflicts)}")
                c_item.setForeground(QColor("#f87171"))
            else:
                c_item = QTableWidgetItem("None")
                c_item.setForeground(QColor("#10b981"))
            c_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            self.mod_table.setItem(row, 5, c_item)

        self.mod_table.blockSignals(False)
        if self.mgr.last_sync:
            self.status.showMessage(self.mgr.last_sync)

        # Update Conflicts Tab
        self.update_conflicts_tab(conflicts)

        # Reselect current mod if possible
        if self.current_selected_mod:
            self.select_mod_by_folder(self.current_selected_mod.folder_name)
        elif len(mods) > 0:
            self.mod_table.selectRow(0)

    def update_conflicts_tab(self, conflicts: dict):
        self.tabs.setTabText(1, f"Conflicts ({len(conflicts)})")
        self.conflict_table.setRowCount(len(conflicts))

        for row, (rel_path, entry) in enumerate(sorted(conflicts.items())):
            f_item = QTableWidgetItem(rel_path)
            w_item = QTableWidgetItem(entry.winning_mod)
            w_item.setForeground(QColor("#10b981"))
            w_item.setFont(QFont("Arial", 11, QFont.Weight.Bold))

            chain_item = QTableWidgetItem(" -> ".join(entry.mods))
            chain_item.setForeground(QColor("#94a3b8"))

            self.conflict_table.setItem(row, 0, f_item)
            self.conflict_table.setItem(row, 1, w_item)
            self.conflict_table.setItem(row, 2, chain_item)

    def select_mod_by_folder(self, folder_name: str):
        for row in range(self.mod_table.rowCount()):
            item = self.mod_table.item(row, 2)
            if item and item.data(Qt.ItemDataRole.UserRole) == folder_name:
                self.mod_table.selectRow(row)
                break

    def on_cell_changed(self, row: int, col: int):
        if col == 0:
            item = self.mod_table.item(row, col)
            name_item = self.mod_table.item(row, 2)
            if not item or not name_item:
                return
            folder_name = name_item.data(Qt.ItemDataRole.UserRole)
            is_checked = item.checkState() == Qt.CheckState.Checked

            mod = self.mgr.get_mod(folder_name)
            if not mod:
                return

            if is_checked != mod.enabled:
                if is_checked:
                    self.mgr.enable_mod(folder_name)
                else:
                    self.mgr.disable_mod(folder_name)
                self.refresh_mods()

    def on_table_selection_changed(self):
        selected_rows = self.mod_table.selectionModel().selectedRows()
        if not selected_rows:
            self.current_selected_mod = None
            return

        row = selected_rows[0].row()
        name_item = self.mod_table.item(row, 2)
        if not name_item:
            return

        folder_name = name_item.data(Qt.ItemDataRole.UserRole)
        mod = self.mgr.get_mod(folder_name)
        self.current_selected_mod = mod
        if not mod:
            return

        # Update details pane
        status_txt = "ENABLED" if mod.enabled else "DISABLED"
        self.detail_title.setText(mod.display_name)
        self.detail_meta.setText(
            f"Author: {mod.metadata.author}  |  Version: {mod.metadata.version}  |  "
            f"Category: {mod.metadata.category}  |  Status: {status_txt}"
        )
        self.detail_desc.setText(mod.metadata.description or "No description provided.")

        self.mod_files_list.clear()
        for f in mod.files:
            self.mod_files_list.addItem(f)

    def on_file_search_filter(self, text: str):
        q = text.lower()
        for i in range(self.mod_files_list.count()):
            item = self.mod_files_list.item(i)
            item.setHidden(q not in item.text().lower())

    def on_table_context_menu(self, pos: QPoint):
        item = self.mod_table.itemAt(pos)
        if not item:
            return
        row = item.row()
        name_item = self.mod_table.item(row, 2)
        if not name_item:
            return
        folder_name = name_item.data(Qt.ItemDataRole.UserRole)
        mod = self.mgr.get_mod(folder_name)
        if not mod:
            return

        menu = QMenu(self)
        toggle_act = menu.addAction("Disable" if mod.enabled else "Enable")
        menu.addSeparator()
        up_act = menu.addAction("Move Priority Up")
        down_act = menu.addAction("Move Priority Down")
        menu.addSeparator()
        open_act = menu.addAction("Open Mod Folder in File Manager")
        delete_act = menu.addAction("Delete Mod")

        action = menu.exec(self.mod_table.viewport().mapToGlobal(pos))
        if action == toggle_act:
            self.mgr.toggle_mod(mod.folder_name)
            self.refresh_mods()
        elif action == up_act:
            self.move_selected_row(-1)
        elif action == down_act:
            self.move_selected_row(1)
        elif action == open_act:
            self.open_path(mod.base_path)
        elif action == delete_act:
            self.on_delete_mod()

    def on_enable_all(self):
        for mod in self.mgr.list_mods():
            if not mod.enabled:
                self.mgr.enable_mod(mod.folder_name)
        self.refresh_mods()

    def on_disable_all(self):
        for mod in self.mgr.list_mods():
            if mod.enabled:
                self.mgr.disable_mod(mod.folder_name)
        self.refresh_mods()

    def on_move_up(self):
        self.move_selected_row(-1)

    def on_move_down(self):
        self.move_selected_row(1)

    def move_selected_row(self, delta: int):
        selected_rows = self.mod_table.selectionModel().selectedRows()
        if not selected_rows:
            return
        row = selected_rows[0].row()
        target_row = row + delta
        if target_row < 0 or target_row >= self.mod_table.rowCount():
            return

        mods = self.mgr.list_mods()
        folder_order = [m.folder_name for m in mods]

        # Swap
        folder_order[row], folder_order[target_row] = folder_order[target_row], folder_order[row]
        self.mgr.reorder_mods(folder_order)
        self.refresh_mods()
        self.mod_table.selectRow(target_row)

    def offer_included(self, always: bool = False):
        """Offers to install (or update) the mods that come with Hookshot Mods. At start (always=False) a mod is
        offered once per version: saying no is remembered."""
        status = [b for b in self.mgr.bundled_status() if b["action"] or always]
        cfg = load_config()
        declined = cfg.get("included_declined", {})
        if not always:
            status = [b for b in status if declined.get(b["name"]) != b["version"]]
        todo = [b for b in status if b["action"]]
        if not status:
            return
        if not todo:
            QMessageBox.information(self, "Included Mods", "\n".join(
                f"{b['name']} {b['version']}: installed" for b in status))
            return
        lines = []
        for b in todo:
            what = f"update {b['installed']} → {b['version']}" if b["action"] == "update" else f"version {b['version']}"
            lines.append(f"• {b['name']} ({what})\n   {b['description']}")
        reply = QMessageBox.question(
            self, "Included Mods",
            "Hookshot Mods comes with these mods:\n\n" + "\n\n".join(lines) +
            "\n\nInstall them into your mods folder now? You can enable, disable or delete them like any other mod.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
        if reply != QMessageBox.StandardButton.Yes:
            for b in todo:
                declined[b["name"]] = b["version"]
            cfg["included_declined"] = declined
            try:
                save_config(cfg)
            except OSError:
                pass
            return
        done, last = [], None
        for b in todo:
            try:
                last = self.mgr.install_mod(b["path"])
                done.append(f"{b['name']} {b['version']}")
            except Exception as e:
                QMessageBox.critical(self, "Included Mods", f"{b['name']}: {e}")
        self.refresh_mods()
        if last:
            self.select_mod_by_folder(last)
        if done:
            self.status.showMessage("Installed: " + ", ".join(done))

    def on_install_mod(self):
        file_path, _ = QFileDialog.getOpenFileName(
            self,
            "Select Mod Archive or Directory",
            "",
            "Mod Archives (*.zip *.7z *.rar *.tar.gz *.tar.xz *.tgz);;All Files (*)",
        )
        if not file_path:
            return

        try:
            folder = self.mgr.install_mod(file_path)
            QMessageBox.information(self, "Success", f"Mod installed successfully:\n{folder}")
            self.refresh_mods()
            self.select_mod_by_folder(folder)
        except Exception as e:
            QMessageBox.critical(self, "Installation Failed", str(e))

    def on_create_mod(self):
        dlg = CreateModDialog(self)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            name = dlg.name_edit.text().strip()
            if not name:
                QMessageBox.warning(self, "Error", "Mod name cannot be empty.")
                return
            try:
                folder = self.mgr.create_mod(
                    name=name,
                    author=dlg.author_edit.text().strip(),
                    version=dlg.version_edit.text().strip(),
                    description=dlg.desc_edit.toPlainText().strip(),
                    category=dlg.category_edit.text().strip(),
                )
                self.refresh_mods()
                self.select_mod_by_folder(folder)
                QMessageBox.information(
                    self,
                    "Mod Created",
                    f"Created mod template:\n{folder}\n\nPlace your RomFS edits inside:\n{os.path.join(self.mgr.mods_dir, folder, 'romfs')}",
                )
            except Exception as e:
                QMessageBox.critical(self, "Creation Failed", str(e))

    def on_delete_mod(self):
        if not self.current_selected_mod:
            QMessageBox.warning(self, "Warning", "Please select a mod to delete.")
            return

        mod = self.current_selected_mod
        reply = QMessageBox.question(
            self,
            "Confirm Delete",
            f"Are you sure you want to permanently delete:\n'{mod.display_name}' ({mod.folder_name})?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply == QMessageBox.StandardButton.Yes:
            try:
                self.mgr.delete_mod(mod.folder_name)
                self.current_selected_mod = None
                self.refresh_mods()
            except Exception as e:
                QMessageBox.critical(self, "Error", str(e))

    def on_open_mods_folder(self):
        self.open_path(self.mgr.mods_dir)

    def open_path(self, path: str):
        if sys.platform == "linux":
            subprocess.Popen(["xdg-open", path], env=child_env())
        elif sys.platform == "darwin":
            subprocess.Popen(["open", path], env=child_env())
        elif sys.platform == "win32":
            os.startfile(path)

    def on_search_romfs(self):
        q = self.romfs_search_input.text().strip()
        if not q:
            return
        results = self.mgr.search_romfs(q, limit=100)
        self.romfs_results_list.clear()
        for r in results:
            self.romfs_results_list.addItem(r)

    def on_extract_to_mod(self):
        if not self.current_selected_mod:
            QMessageBox.warning(self, "Warning", "Please select a target mod from the list first.")
            return

        item = self.romfs_results_list.currentItem()
        if not item:
            QMessageBox.warning(self, "Warning", "Please select an asset from the search results.")
            return

        rel_path = item.text()
        dest_path = os.path.join(self.current_selected_mod.base_path, "romfs", rel_path)
        # If .zs, ask if user wants it decompressed
        decompress = rel_path.lower().endswith(".zs")

        try:
            size = self.mgr.extract_game_asset(rel_path, dest_path, decompress=decompress)
            QMessageBox.information(
                self,
                "Success",
                f"Extracted to mod:\n{dest_path}\n({size} bytes)",
            )
            self.refresh_mods()
        except Exception as e:
            QMessageBox.critical(self, "Extraction Error", str(e))

    def on_extract_to_file(self):
        item = self.romfs_results_list.currentItem()
        if not item:
            QMessageBox.warning(self, "Warning", "Please select an asset from the search results.")
            return

        rel_path = item.text()
        default_name = os.path.basename(rel_path)
        dest_file, _ = QFileDialog.getSaveFileName(self, "Save Asset", default_name)
        if not dest_file:
            return

        decompress = rel_path.lower().endswith(".zs")
        try:
            size = self.mgr.extract_game_asset(rel_path, dest_file, decompress=decompress)
            QMessageBox.information(self, "Success", f"Saved {size} bytes to:\n{dest_file}")
        except Exception as e:
            QMessageBox.critical(self, "Extraction Error", str(e))

    def on_launch_game(self):
        try:
            overlay_enabled = self.overlay_checkbox.isVisible() and self.overlay_checkbox.isChecked()
            self.mgr.launch_game(overlay=overlay_enabled)
            codes = [m.display_name for m in self.mgr.code_mods() if m.enabled and not m.code_layer.problem]
            status_text = f"{runtime_title(self.mgr.runtime)} launched" + (f" with code mods: {', '.join(codes)}" if codes else "") + \
                          (" + F2 dev overlay." if overlay_enabled else ".")
            self.status.showMessage(status_text)
        except Exception as e:
            QMessageBox.critical(self, "Launch Error", f"Failed to launch game: {e}")


def run_gui(game_dir: Optional[str] = None) -> int:
    app = QApplication.instance()
    if not app:
        app = QApplication(sys.argv)
    window = MainWindow(game_dir)
    window.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(run_gui())
