from __future__ import annotations
from datetime import datetime
import ctypes
import json
import logging
import os
from pathlib import Path
import subprocess
import sys
import time
import uuid
from PySide6 import QtCore as C, QtGui as G, QtWidgets as W
from . import __version__
from .appearance import AppearanceMixin
from . import color_theme
from .displays import detect_monitors
from .models import dimension, engine_settings, resolution_warnings, suggest_layout
from .native import OwnedJob
from .logging_widgets import HtmlFormatter, LogStringHandler, LogTextBrowser, SessionTextLogHandler
from .widgets import NoWheelComboBox
from .utils import APP_NAME, WINDOW_TITLE, APP_RUNTIME_DIR, load_app_state, save_app_state, desktop_dir, default_output_dir

class MonitorMap(W.QWidget):
    selection_changed = C.Signal(object)
    def __init__(self, monitors, selected, parent=None):
        super().__init__(parent); self.monitors = monitors; self.selected = list(selected); self.hit_boxes = []; self.setMinimumHeight(145)
        self._painting=False;self._paint_names=[];self._last_point=None
    def paintEvent(self, event):
        if not self.monitors: return
        p = G.QPainter(self); p.setRenderHint(G.QPainter.RenderHint.Antialiasing)
        lowx=min(m.x for m in self.monitors); lowy=min(m.y for m in self.monitors)
        width=max(m.right for m in self.monitors)-lowx; height=max(m.y+m.height for m in self.monitors)-lowy
        scale=min((self.width()-24)/width, (self.height()-18)/height)
        ox=(self.width()-width*scale)/2; oy=(self.height()-height*scale)/2
        self.hit_boxes=[]
        for index,m in enumerate(self.monitors):
            r=C.QRectF(ox+(m.x-lowx)*scale+2,oy+(m.y-lowy)*scale+2,m.width*scale-4,m.height*scale-4)
            self.hit_boxes.append((r,m.name))
            p.setBrush(G.QColor(color_theme.ui_color("button_background" if m.name in self.selected else "input_background")))
            p.setPen(G.QPen(G.QColor(color_theme.ui_color("strong_border")),1));p.drawRoundedRect(r,5,5)
            label=m.display_id if m.display_id is not None else index+1
            p.setPen(G.QColor(color_theme.ui_color("text")));p.drawText(r,C.Qt.AlignmentFlag.AlignCenter,f"{label}{' • Primary' if m.primary else ''}\n{m.width} × {m.height}")
    def mousePressEvent(self,event):
        if event.button()==C.Qt.MouseButton.LeftButton:
            for rect,name in self.hit_boxes:
                if rect.contains(event.position()):
                    self._painting=True;self._paint_names=[name];self._last_point=event.position()
                    self.selected=list(self._paint_names);self.update();self.selection_changed.emit(list(self._paint_names));event.accept();return
        super().mousePressEvent(event)
    def mouseMoveEvent(self,event):
        if self._painting and event.buttons()&C.Qt.MouseButton.LeftButton:
            path=G.QPainterPath(self._last_point);path.lineTo(event.position())
            stroker=G.QPainterPathStroker();stroker.setWidth(12);band=stroker.createStroke(path)
            changed=False
            for rect,name in self.hit_boxes:
                if name not in self._paint_names and (rect.contains(event.position()) or band.intersects(rect)):
                    self._paint_names.append(name);changed=True
            self._last_point=event.position()
            if changed:
                self.selected=list(self._paint_names);self.update();self.selection_changed.emit(list(self._paint_names))
            event.accept();return
        super().mouseMoveEvent(event)
    def mouseReleaseEvent(self,event):
        if self._painting and event.button()==C.Qt.MouseButton.LeftButton:
            self._painting=False;self._last_point=None;event.accept();return
        super().mouseReleaseEvent(event)

class NessaOverlay(W.QWidget):
    def __init__(self,parent):
        super().__init__(parent);self._progress=0.0;self.setAttribute(C.Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        root=Path(getattr(sys,"_MEIPASS",Path(__file__).resolve().parents[1]));self.source=G.QPixmap(str(root/"assets"/"unamused_nessa.png"))
        self.scaled=G.QPixmap();self.hide()
    def getProgress(self):return self._progress
    def setProgress(self,value):self._progress=max(0.0,min(1.0,float(value)));self.reposition();self.update()
    progress=C.Property(float,getProgress,setProgress)
    def reposition(self):
        if self.source.isNull() or self._progress<=0:return
        size=min(360,max(230,int(self.parentWidget().height()*.48)))
        self.scaled=self.source.scaled(size,size,C.Qt.AspectRatioMode.KeepAspectRatio,C.Qt.TransformationMode.SmoothTransformation)
        self.setGeometry(0,self.parentWidget().height()-size,size,size);self.show();self.raise_()
    def paintEvent(self,event):
        if self.scaled.isNull():return
        p=G.QPainter(self);p.setRenderHint(G.QPainter.RenderHint.SmoothPixmapTransform)
        object_y=self.height()-round(self.scaled.height()*self._progress)
        p.drawPixmap(0,object_y,self.scaled)

class MainWindow(AppearanceMixin, W.QMainWindow):
    def __init__(self, discover=True):
        super().__init__(); self.setWindowTitle(WINDOW_TITLE); self.resize(1020,820); self.setMinimumSize(820,680)
        self.state=load_app_state(); self.monitors=detect_monitors(); self.layout=suggest_layout(self.monitors)
        if self.state.get("custom_colors"): color_theme.sync_active_colors({"colors":self.state["custom_colors"]});color_theme.apply_application_theme()
        self.discovery=None; self.job=None; self.job_guard=None;self.discovery_guard=None;self.manual_path=False
        self.result=None;self.busy=False;self.backup_bad=False;self.commit_started=False;self.launch_after=False
        self.target_paths=dict(self.state.get("target_paths",{}));self.target_kind=self.state.get("target","terraria")
        self.monitor_signature=self.display_signature(self.monitors);self.nessa_clicks=[];self.nessa_target=0.0
        self.selected_display_names=list(self.layout["monitors"])
        central=W.QWidget();self.setCentralWidget(central);root=W.QVBoxLayout(central);root.setContentsMargins(14,12,14,12);root.setSpacing(10)
        paths=W.QWidget();grid=W.QGridLayout(paths);grid.setContentsMargins(0,0,0,0)
        self.target=NoWheelComboBox();self.target.addItem("Terraria","terraria");self.target.addItem("tModLoader","tmodloader")
        self.target.setCurrentIndex(max(0,self.target.findData(self.target_kind)));self.target.currentIndexChanged.connect(self.target_changed)
        grid.addWidget(W.QLabel("Game"),0,0);grid.addWidget(self.target,0,1,1,3)
        initial=self.target_paths.get(self.target_kind,self.state.get("exe",""))
        self.game=W.QLineEdit(str(initial));self.game.setPlaceholderText("Searching Steam libraries… or choose an installation")
        self.game.installEventFilter(self);self.game.textEdited.connect(self.manual_game);self.game.textChanged.connect(self.invalidate)
        self.game_browse=W.QPushButton("Browse…");self.game_browse.clicked.connect(self.browse_game)
        self.game_open=W.QPushButton("Open");self.game_open.clicked.connect(self.open_game_location)
        self.game_label=W.QLabel("Installation");grid.addWidget(self.game_label,1,0);grid.addWidget(self.game,1,1);grid.addWidget(self.game_browse,1,2);grid.addWidget(self.game_open,1,3)
        saved_output=str(self.state.get("backup_root","")).strip()
        if not saved_output or Path(saved_output)==desktop_dir():saved_output=str(default_output_dir())
        self.backup=W.QLineEdit(saved_output);self.backup.setPlaceholderText("Required before patching — choose a safe backup folder")
        self.backup.textChanged.connect(self.backup_changed)
        self.backup_browse=W.QPushButton("Browse…");self.backup_browse.clicked.connect(self.browse_backup)
        self.backup_open=W.QPushButton("Open");self.backup_open.clicked.connect(self.open_backup_location)
        grid.addWidget(W.QLabel("Backup/Output"),2,0);grid.addWidget(self.backup,2,1);grid.addWidget(self.backup_browse,2,2);grid.addWidget(self.backup_open,2,3)
        self.desktop=W.QPushButton("Use Desktop");desktop_policy=self.desktop.sizePolicy();desktop_policy.setRetainSizeWhenHidden(True);self.desktop.setSizePolicy(desktop_policy)
        self.desktop.hide();self.desktop.clicked.connect(lambda:self.backup.setText(str(desktop_dir())))
        self.desktop_slot=W.QWidget();desktop_layout=W.QVBoxLayout(self.desktop_slot);desktop_layout.setContentsMargins(0,0,0,0);desktop_layout.addWidget(self.desktop)
        self.desktop_slot.setFixedHeight(max(34,self.desktop.sizeHint().height()))
        self.backup_note=W.QLabel("");self.backup_note.setWordWrap(True);self.backup_note.setFixedHeight(34)
        grid.addWidget(self.backup_note,3,1);grid.addWidget(self.desktop_slot,3,2);root.addWidget(paths)
        self.tabs=W.QTabWidget();root.addWidget(self.tabs,1)
        self.patch_page=self.build_patch_page();self.tabs.addTab(self.patch_page,"Patch")
        self.tabs.addTab(self.build_displays_page(),"Displays")
        self.log_widget=LogTextBrowser();self.log_widget.setFont(G.QFont("Consolas",9));self.log_page=self.log_widget;self.tabs.addTab(self.log_widget,"Log")
        self.tabs.addTab(self.build_options(),"Options")
        footer=W.QHBoxLayout();root.addLayout(footer)
        self.patch_play=W.QPushButton("Patch && Launch Terraria");self.patch_only=W.QPushButton("Patch && Launch tModLoader")
        self.restore_button=W.QPushButton("Restore backup")
        self.launch=W.QPushButton("Launch");self.cancel=W.QPushButton("Cancel");self.cancel.hide()
        self.print_log=W.QPushButton("Print log to output");self.copy_log=W.QPushButton("Copy log to clipboard")
        self.admin=W.QPushButton("Restart as administrator");self.admin.hide();self.admin.clicked.connect(self.restart_admin)
        for button in (self.patch_play,self.patch_only,self.restore_button,self.launch,self.cancel,self.print_log,self.copy_log,self.show_all):footer.addWidget(button)
        footer.addStretch(1);self.status=W.QLabel("Ready");self.status.setAlignment(C.Qt.AlignmentFlag.AlignRight|C.Qt.AlignmentFlag.AlignVCenter);footer.addWidget(self.status)
        root.addWidget(self.admin,0,C.Qt.AlignmentFlag.AlignLeft)
        self.patch_play.clicked.connect(lambda:self.begin_patch_target("terraria"));self.patch_only.clicked.connect(lambda:self.begin_patch_target("tmodloader"))
        self.restore_button.clicked.connect(self.choose_restore)
        self.launch.clicked.connect(self.begin_launch);self.cancel.clicked.connect(self.cancel_job)
        self.print_log.clicked.connect(self.print_session);self.copy_log.clicked.connect(lambda:W.QApplication.clipboard().setText(self.log_widget.toPlainText()))
        self.tabs.currentChanged.connect(self.tab_actions)
        self.log_handler=LogStringHandler();self.log_handler.setFormatter(HtmlFormatter());self.log_handler.log_signal.connect(self.log_widget.appendHtml)
        self.session_handler=SessionTextLogHandler();self.session_handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
        logging.getLogger().addHandler(self.log_handler);logging.getLogger().addHandler(self.session_handler)
        self.nessa=NessaOverlay(self.patch_page);self.nessa_animation=C.QPropertyAnimation(self.nessa,b"progress",self);self.nessa_animation.setDuration(720);self.nessa_animation.setEasingCurve(C.QEasingCurve.Type.OutCubic)
        self.apply_saved_settings();self.update_target_ui();self.set_log_hidden(bool(self.state.get("hide_log",False)));self.update_warning();self.tab_actions();self.width_edit.setFocus()
        logging.info("Detected %s displays. Suggested resolution: %s × %s.",len(self.monitors),self.layout["width"],self.layout["height"])
        logging.info("Razorbeam Terraria Patcher %s is ready. Settings and HUD dimensions follow the selected display.",__version__)
        if discover:C.QTimer.singleShot(150,self.start_discovery)
        self.display_timer=C.QTimer(self);self.display_timer.timeout.connect(self.watch_displays);self.display_timer.start(1500)

    def build_patch_page(self):
        page=W.QWidget();layout=W.QVBoxLayout(page);layout.setContentsMargins(14,12,14,12)
        row=W.QHBoxLayout();row.addWidget(W.QLabel("Game resolution"))
        self.width_edit=W.QLineEdit(str(self.layout["width"]));self.height_edit=W.QLineEdit(str(self.layout["height"]))
        for field in (self.width_edit,self.height_edit):
            field.setMaxLength(10);field.setMinimumHeight(38);field.setMaximumWidth(190);field.setAlignment(C.Qt.AlignmentFlag.AlignCenter)
            field.setStyleSheet("QLineEdit { font-size: 21px; font-weight: bold; }");field.textChanged.connect(self.update_warning)
        row.addWidget(self.width_edit);row.addWidget(W.QLabel("×"));row.addWidget(self.height_edit);row.addStretch()
        detected=W.QPushButton("Use detected layout");detected.clicked.connect(lambda:self.use_detected(True));row.addWidget(detected);layout.addLayout(row)
        mode_row=W.QHBoxLayout();mode_row.addWidget(W.QLabel("Window mode"));self.mode=NoWheelComboBox()
        self.mode.addItem("Borderless — span displays",1);self.mode.addItem("Windowed — with title bar",0);self.mode.addItem("Exclusive fullscreen",2)
        self.mode.currentIndexChanged.connect(self.update_warning);mode_row.addWidget(self.mode,1);layout.addLayout(mode_row)
        self.stable_title=W.QCheckBox("Disable title messages");self.stable_title.setChecked(True)
        self.tooltip(self.stable_title,'Keep window title "Terraria"')
        layout.addWidget(self.stable_title)
        self.skip_splash=W.QCheckBox("Skip startup splash");self.skip_splash.setChecked(True)
        layout.addWidget(self.skip_splash)
        self.center_splash=W.QCheckBox("Center startup art on selected display");self.center_splash.setChecked(bool(self.layout.get("triple")))
        layout.addWidget(self.center_splash)
        self.skip_splash.toggled.connect(lambda checked:self.center_splash.setEnabled(not checked and self.target_kind!="tmodloader"))
        self.centered=W.QCheckBox("Centered UI");layout.addWidget(self.centered)
        self.tooltip(self.centered,"Centers inventory, health bar, etc. to center of viewport")
        self.centered.toggled.connect(self.centered_changed)
        self.ui_panel=W.QGroupBox("Interface viewport — relative to the game window",page);ui_grid=W.QGridLayout(self.ui_panel)
        self.ui_monitor=NoWheelComboBox();self.ui_monitor.addItem("Custom rectangle",None)
        for i,m in enumerate(self.monitors):self.ui_monitor.addItem(f"Display {m.display_id or i+1} — {m.width} × {m.height}",m.name)
        ui_grid.addWidget(W.QLabel("UI display"),0,0);ui_grid.addWidget(self.ui_monitor,0,1,1,7);self.ui_monitor.currentIndexChanged.connect(self.select_ui_monitor)
        self.ui_fields={}
        for i,(key,label,default) in enumerate((("ui_x","X",self.layout["ui_x"]),("ui_y","Y",self.layout["ui_y"]),
                                               ("ui_width","Width",self.layout["ui_width"]),("ui_height","Height",self.layout["ui_height"]))):
            field=W.QLineEdit(str(default));field.setMaxLength(10);field.textEdited.connect(lambda:self.ui_monitor.setCurrentIndex(0));self.ui_fields[key]=field
            ui_grid.addWidget(W.QLabel(label),1,i*2);ui_grid.addWidget(field,1,i*2+1)
        self.ui_panel.hide()
        self.version_label=W.QLabel("Terraria Version: Detecting…");self.version_label.setTextInteractionFlags(C.Qt.TextInteractionFlag.TextSelectableByMouse);layout.addWidget(self.version_label)
        self.warning=W.QLabel();self.warning.hide()
        self.analysis_text=W.QLabel();self.analysis_text.hide()
        self.activity=W.QLabel();self.activity.hide()
        layout.addStretch()
        return page

    def build_displays_page(self):
        page=W.QWidget();layout=W.QVBoxLayout(page)
        controls=W.QHBoxLayout();controls.addStretch(1)
        self.use_detected_button=W.QPushButton("Use detected layout");self.use_detected_button.clicked.connect(lambda:self.use_detected(True));controls.addWidget(self.use_detected_button)
        self.refresh_displays_button=W.QPushButton("Refresh");self.refresh_displays_button.clicked.connect(self.refresh_displays);controls.addWidget(self.refresh_displays_button);layout.addLayout(controls)
        self.monitor_map=MonitorMap(self.monitors,self.layout["monitors"]);self.monitor_map.selection_changed.connect(self.select_displays);layout.addWidget(self.monitor_map)
        text=("Triplewide setup detected. Other monitors ignored." if self.layout["triple"] else "Primary monitor selected. No clear triplewide row.")
        self.display_note=W.QLabel(text);self.display_note.setWordWrap(True);layout.addWidget(self.display_note)
        self.display_table=W.QTableWidget(len(self.monitors),5);self.display_table.setHorizontalHeaderLabels(["Display","Resolution","Desktop X","Desktop Y","Primary"])
        for row,m in enumerate(self.monitors):
            label=m.display_id if m.display_id is not None else row+1
            for col,value in enumerate((str(label),f"{m.width} × {m.height}",str(m.x),str(m.y),"Yes" if m.primary else "")):
                self.display_table.setItem(row,col,W.QTableWidgetItem(value))
        self.display_table.setSelectionBehavior(W.QAbstractItemView.SelectionBehavior.SelectRows);self.display_table.setSelectionMode(W.QAbstractItemView.SelectionMode.ExtendedSelection)
        self.display_table.setEditTriggers(W.QAbstractItemView.EditTrigger.NoEditTriggers);self.display_table.horizontalHeader().setSectionResizeMode(W.QHeaderView.ResizeMode.Stretch)
        self.display_table.cellClicked.connect(lambda row,_col:self.select_display(self.monitors[row].name))
        layout.addWidget(self.display_table,1)
        row=W.QHBoxLayout();origin_label=W.QLabel("Game window origin (physical desktop pixels)");row.addWidget(origin_label)
        self.window_x=W.QLineEdit(str(self.layout["x"]));self.window_y=W.QLineEdit(str(self.layout["y"]))
        for label,field in (("X",self.window_x),("Y",self.window_y)):row.addWidget(W.QLabel(label));row.addWidget(field)
        origin_tip="Coordinates of the game window's top-left outer corner on the Windows virtual desktop. 0,0 is the primary display's top-left. Displays left of or above it use negative values."
        self.tooltip(origin_label,origin_tip);self.tooltip(self.window_x,origin_tip);self.tooltip(self.window_y,origin_tip)
        layout.addLayout(row)
        note=W.QLabel("Negative coordinates supported. Borderless spans independent monitors.");note.setWordWrap(True);layout.addWidget(note)
        return page

    @staticmethod
    def display_signature(monitors):
        return tuple((m.name,m.display_id,m.x,m.y,m.width,m.height,m.primary) for m in monitors)

    def watch_displays(self):
        try:current=detect_monitors()
        except OSError:return
        if self.display_signature(current)!=self.monitor_signature:
            if hasattr(self,"refresh_flash_timer") and self.refresh_flash_timer.isActive():return
            self.refresh_displays_button.setText("DISPLAY CHANGED — REFRESH")
            self.refresh_flash_phase=False;self.refresh_flash_timer=C.QTimer(self);self.refresh_flash_timer.timeout.connect(self.flash_refresh)
            self.refresh_flash_timer.start(260);self.flash_refresh()

    def flash_refresh(self):
        self.refresh_flash_phase=not self.refresh_flash_phase
        purple=color_theme.ui_color("button_background")
        self.refresh_displays_button.setStyleSheet(
            f"QPushButton {{ background: {'#ffffff' if self.refresh_flash_phase else purple}; color: {'#000000' if self.refresh_flash_phase else '#ffffff'}; border: 3px solid {purple}; font-weight: normal; padding: 7px 12px; }}")

    def refresh_displays(self):
        prior=set(self.selected_display_names);self.monitors=detect_monitors();self.monitor_signature=self.display_signature(self.monitors);self.layout=suggest_layout(self.monitors)
        self.selected_display_names=[m.name for m in self.monitors if m.name in prior] or list(self.layout["monitors"])
        self.monitor_map.monitors=self.monitors;self.monitor_map.selected=self.selected_display_names;self.monitor_map.update()
        text=("Triplewide setup detected. Other monitors ignored." if self.layout["triple"] else "Primary monitor selected. No clear triplewide row.")
        self.display_note.setText(text);self.display_table.setRowCount(len(self.monitors))
        for row,m in enumerate(self.monitors):
            label=m.display_id if m.display_id is not None else row+1
            for col,value in enumerate((str(label),f"{m.width} × {m.height}",str(m.x),str(m.y),"Yes" if m.primary else "")):
                self.display_table.setItem(row,col,W.QTableWidgetItem(value))
        self.ui_monitor.blockSignals(True);self.ui_monitor.clear();self.ui_monitor.addItem("Custom rectangle",None)
        for i,m in enumerate(self.monitors):self.ui_monitor.addItem(f"Display {m.display_id or i+1} — {m.width} × {m.height}",m.name)
        self.ui_monitor.blockSignals(False)
        if hasattr(self,"refresh_flash_timer"):self.refresh_flash_timer.stop()
        self.refresh_displays_button.setText("Refresh");self.refresh_displays_button.setStyleSheet("")

    def apply_saved_settings(self):
        saved=self.state.get("settings",{})
        for key,field in (("Width",self.width_edit),("Height",self.height_edit),("WindowX",self.window_x),("WindowY",self.window_y)):
            if key in saved:field.setText(str(saved[key]))
        restore_ui=bool(saved.get("UiEnabled"))
        for key,field in self.ui_fields.items():
            mapped={"ui_x":"UiX","ui_y":"UiY","ui_width":"UiWidth","ui_height":"UiHeight"}[key]
            field.setText(str(saved[mapped] if restore_ui and mapped in saved else self.layout[key]))
        self.mode.setCurrentIndex(max(0,self.mode.findData(saved.get("Mode",1))))
        self.stable_title.setChecked(bool(saved.get("StableTitle",1)));self.centered.setChecked(False)
        self.skip_splash.setChecked(bool(saved.get("SkipSplash",True)))
        self.center_splash.setChecked(bool(saved.get("SplashEnabled",self.layout.get("triple",False))))
        self.ui_monitor.setCurrentIndex(max(0,self.ui_monitor.findData(self.layout.get("ui_monitor"))))

    def target_changed(self):
        old=self.target_kind;self.target_paths[old]=self.game.text();self.target_kind=self.target.currentData()
        self.game.blockSignals(True);self.game.setText(self.target_paths.get(self.target_kind,""));self.game.blockSignals(False)
        self.manual_path=False;self.result=None;self.update_target_ui()
        if not self.game.text().strip():self.start_discovery()
        else:self.start_job("analyze")

    def update_target_ui(self):
        tml=self.target_kind=="tmodloader"
        self.game.setPlaceholderText("Finding tModLoader automatically… or choose its folder" if tml else "Finding Terraria automatically… or choose Terraria.exe")
        self.stable_title.setText("Disable title messages")
        self.tooltip(self.stable_title,'Keep window title "Razorbeam All-in-One Resolution Patcher for Terraria"' if tml else 'Keep window title "Terraria"')
        self.centered.setEnabled(True)
        self.center_splash.setEnabled(not tml and not self.skip_splash.isChecked());self.skip_splash.setEnabled(True)
        if tml:
            self.analysis_text.setText("tModLoader detected. Razorbeam installs a client-side display bridge. Centered UI covers vanilla and mod-added interface layers.")
        self.tooltip(self.centered,"Centers inventory, health bar, etc. to center of viewport")

    def use_detected(self,redetect=True):
        if redetect:self.layout=suggest_layout(detect_monitors())
        self.selected_display_names=list(self.layout["monitors"])
        self.width_edit.setText(str(self.layout["width"]));self.height_edit.setText(str(self.layout["height"]))
        self.window_x.setText(str(self.layout["x"]));self.window_y.setText(str(self.layout["y"]))
        for key,field in self.ui_fields.items():field.setText(str(self.layout[key]))
        self.mode.setCurrentIndex(0);self.ui_monitor.setCurrentIndex(max(0,self.ui_monitor.findData(self.layout["ui_monitor"])))
        if hasattr(self,"monitor_map"):self.monitor_map.selected=self.selected_display_names;self.monitor_map.update()
        if hasattr(self,"display_table"):self.display_table.clearSelection()
        if hasattr(self,"display_note"):self.display_note.setText("Triplewide setup detected. Other monitors ignored." if self.layout["triple"] else "Primary monitor selected. No clear triplewide row.")

    def select_display(self,name):
        self.select_displays([name])

    def preferred_monitor(self, selected=None):
        selected=selected or [m for m in self.monitors if m.name in self.selected_display_names]
        if not selected:return None
        primary=next((m for m in selected if m.primary),None)
        if primary:return primary
        left=min(m.x for m in selected);top=min(m.y for m in selected);right=max(m.right for m in selected);bottom=max(m.y+m.height for m in selected)
        cx=(left+right)/2;cy=(top+bottom)/2
        return min(selected,key=lambda m:((m.x+m.width/2-cx)**2+(m.y+m.height/2-cy)**2,m.x,m.y))

    def centered_changed(self,checked):
        if checked:
            monitor=self.preferred_monitor()
            try:x=int(self.window_x.text());y=int(self.window_y.text())
            except ValueError:monitor=None
            if monitor:
                for key,value in {"ui_x":monitor.x-x,"ui_y":monitor.y-y,"ui_width":monitor.width,"ui_height":monitor.height}.items():self.ui_fields[key].setText(str(value))
                self.ui_monitor.setCurrentIndex(max(0,self.ui_monitor.findData(monitor.name)))
        self.update_warning()

    def select_displays(self,names):
        requested=set(names);selected=[m for m in self.monitors if m.name in requested]
        if not selected:return
        self.selected_display_names=[m.name for m in selected]
        left=min(m.x for m in selected);top=min(m.y for m in selected);right=max(m.right for m in selected);bottom=max(m.y+m.height for m in selected)
        self.width_edit.setText(str(right-left));self.height_edit.setText(str(bottom-top))
        self.window_x.setText(str(left));self.window_y.setText(str(top));self.mode.setCurrentIndex(0)
        preferred=self.preferred_monitor(selected)
        for key,value in {"ui_x":preferred.x-left,"ui_y":preferred.y-top,"ui_width":preferred.width,"ui_height":preferred.height}.items():self.ui_fields[key].setText(str(value))
        self.ui_monitor.setCurrentIndex(max(0,self.ui_monitor.findData(preferred.name)))
        self.monitor_map.selected=list(self.selected_display_names);self.monitor_map.update()
        self.display_table.blockSignals(True);self.display_table.clearSelection()
        for row,m in enumerate(self.monitors):
            if m.name in requested:
                for col in range(self.display_table.columnCount()):
                    item=self.display_table.item(row,col)
                    if item:item.setSelected(True)
        self.display_table.blockSignals(False)
        if len(selected)==1:
            row=self.monitors.index(selected[0]);label=selected[0].display_id if selected[0].display_id is not None else row+1
            text=f"Display {label} selected. Game resolution updated."
        elif len(selected)==2:text="2 displays selected. Center bezel crosses the game image."
        else:text=f"{len(selected)} displays selected. Game resolution updated."
        self.display_note.setText(text);self.update_warning()

    def select_ui_monitor(self):
        if not hasattr(self,"ui_fields") or not hasattr(self,"window_x"):return
        name=self.ui_monitor.currentData();monitor=next((m for m in self.monitors if m.name==name),None)
        if monitor:
            try:x=int(self.window_x.text());y=int(self.window_y.text())
            except ValueError:return
            for key,value in {"ui_x":monitor.x-x,"ui_y":monitor.y-y,"ui_width":monitor.width,"ui_height":monitor.height}.items():self.ui_fields[key].setText(str(value))

    def settings(self):
        w=dimension(self.width_edit.text());h=dimension(self.height_edit.text())
        try:x=int(self.window_x.text());y=int(self.window_y.text())
        except ValueError:raise ValueError("Window origin must contain whole numbers; negative desktop coordinates are allowed.")
        splash=self.preferred_monitor()
        centered=self.centered.isChecked()
        if centered and not splash:raise ValueError("Centered UI requires a selected display.")
        u={"ui_x":splash.x-x,"ui_y":splash.y-y,"ui_width":splash.width,"ui_height":splash.height} if centered else {"ui_x":0,"ui_y":0,"ui_width":w,"ui_height":h}
        preferred=splash.name if splash else ""
        sx,sy,sw,sh=(splash.x-x,splash.y-y,splash.width,splash.height) if splash else (0,0,w,h)
        center_splash=self.center_splash.isChecked() and self.target_kind!="tmodloader" and sx>=0 and sy>=0 and sx+sw<=w and sy+sh<=h
        if not center_splash:sx,sy,sw,sh=0,0,w,h
        return engine_settings(w,h,x,y,centered,**u,mode=self.mode.currentData(),stable_title=self.stable_title.isChecked(),
                               display_screen=preferred,skip_splash=self.skip_splash.isChecked(),center_splash=center_splash,
                               splash_x=sx,splash_y=sy,splash_width=sw,splash_height=sh,
                               prevent_minimize=self.prevent_minimize.isChecked())

    def update_warning(self):
        if not hasattr(self,"warning"):return
        try:
            w=dimension(self.width_edit.text());h=dimension(self.height_edit.text());warnings=resolution_warnings(w,h)
            if self.mode.currentData()==2:warnings.append("Exclusive fullscreen requires a driver-supported display mode. Arbitrary sizes or independent-monitor spans may be rejected or changed by the game/driver.")
            if len(self.selected_display_names)==2:warnings.append("Two displays selected. A bezel will cross the center of the game image.")
            self.warning.setText("\n".join(warnings) or "Custom dimensions accepted. Actual rendering depends on Terraria, XNA, and the graphics hardware.")
        except ValueError as e:self.warning.setText(str(e))
        self.refresh_status_styles()

    def refresh_status_styles(self):
        if hasattr(self,"warning"):self.warning.setStyleSheet(f"QLabel {{ color: {color_theme.ui_color('warning')}; }}")
        if hasattr(self,"backup") and self.backup_bad:self.backup.setStyleSheet("QLineEdit { background: #ff0000; color: #ffffff; border: 2px solid #ff0000; font-weight: bold; }")

    def backup_changed(self):
        self.backup_bad=False;self.backup.setStyleSheet("");self.backup_note.setText("")
        if hasattr(self,"status") and not self.busy:self.status.setText("Ready")

    def require_backup(self):
        raw=self.backup.text().strip()
        if raw and Path(os.path.expandvars(raw)).expanduser().is_dir():
            self.backup.setText(str(Path(os.path.expandvars(raw)).expanduser()));return True
        self.backup_bad=True;self.refresh_status_styles();self.backup_note.setText("Backup location required. No patch or launch can proceed.")
        self.backup.setFocus()
        if hasattr(self,"shake") and self.shake.state()==C.QAbstractAnimation.State.Running:
            self.shake.stop();self.backup.move(self.shake_origin)
        origin=self.backup.pos();self.shake_origin=origin;self.shake=C.QPropertyAnimation(self.backup,b"pos",self);self.shake.setDuration(420)
        for t,dx in ((0,0),(.12,-9),(.25,9),(.38,-8),(.51,8),(.64,-5),(.77,5),(.9,-2),(1,0)):self.shake.setKeyValueAt(t,origin+C.QPoint(dx,0))
        self.shake.finished.connect(self.desktop.show);self.shake.start();self.status.setText("Backup required")
        return False

    def eventFilter(self,obj,event):
        if obj is getattr(self,"game",None) and event.type()==C.QEvent.Type.FocusIn:self.manual_game()
        return super().eventFilter(obj,event)

    def manual_game(self,*args):
        self.manual_path=True;self.stop_discovery()

    def invalidate(self):self.result=None

    def browse_game(self):
        self.manual_game()
        if self.target_kind=="tmodloader":
            chosen=W.QFileDialog.getExistingDirectory(self,"Select tModLoader installation",self.game.text())
        else:chosen,_=W.QFileDialog.getOpenFileName(self,"Select Terraria.exe",self.game.text(),"Terraria executable (Terraria.exe)")
        if chosen:self.game.setText(chosen);self.start_job("analyze")

    def browse_backup(self):
        chosen=W.QFileDialog.getExistingDirectory(self,"Choose mandatory backup folder",self.backup.text() or str(desktop_dir()))
        if chosen:self.backup.setText(chosen)

    def open_location(self,raw,file_parent=False):
        path=Path(os.path.expandvars(str(raw).strip().strip('"'))).expanduser()
        if file_parent or path.is_file():path=path.parent
        if not path.is_dir():self.fail("Folder does not exist: "+str(path));return
        if not G.QDesktopServices.openUrl(C.QUrl.fromLocalFile(str(path.resolve()))):self.fail("Windows could not open: "+str(path))

    def open_game_location(self):self.open_location(self.game.text(),self.target_kind=="terraria")
    def open_backup_location(self):self.open_location(self.backup.text())

    def command(self):
        return [sys.executable] if getattr(sys,"frozen",False) else [sys.executable,str(Path(__file__).resolve().parents[1]/"app.py")]

    def make_process(self,request):
        jobs=APP_RUNTIME_DIR/"jobs";jobs.mkdir(parents=True,exist_ok=True);file=jobs/(uuid.uuid4().hex+".json")
        file.write_text(json.dumps(request),encoding="utf-8")
        process=C.QProcess(self);cmd=self.command();process.setProgram(cmd[0]);process.setArguments([*cmd[1:],"--worker",str(file)])
        process.request_file=file;process.events_file=Path(str(file)+".events");process.events_file.touch()
        process.buffer=b"";process.received=False;process.event_offset=0
        process.watcher=C.QFileSystemWatcher([str(process.events_file)],process)
        process.watcher.fileChanged.connect(lambda:self.read_messages(process,request["action"]))
        process.guard=None
        def contain():
            try:
                process.guard=OwnedJob();process.guard.assign(int(process.processId()))
            except OSError as exc:
                process.kill()
                logging.error("Could not contain the worker process: %s",exc)
        process.started.connect(contain)
        return process

    def start_discovery(self):
        if self.manual_path:return
        if self.game.text().strip() and Path(self.game.text()).exists():self.start_job("analyze");return
        self.discovery=self.make_process({"action":"locate"});p=self.discovery
        p.readyReadStandardOutput.connect(lambda:self.read_messages(p,"locate"));p.finished.connect(lambda:self.discovery_finished(p));p.start()

    def stop_discovery(self):
        if self.discovery and self.discovery.state()!=C.QProcess.ProcessState.NotRunning:
            if self.discovery.guard:self.discovery.guard.close()
            self.discovery.kill()

    def discovery_finished(self,p):
        self.read_messages(p,"locate")
        if p==self.discovery:self.discovery=None
        if p.guard:p.guard.close()
        p.watcher.removePaths(p.watcher.files());p.request_file.unlink(missing_ok=True);p.events_file.unlink(missing_ok=True);p.deleteLater()

    def start_job(self,action,**extra):
        if self.busy:return
        if not self.game.text().strip():self.fail(("tModLoader" if self.target_kind=="tmodloader" else "Terraria")+" installation not selected yet.");return
        self.stop_discovery();self.busy=True;self.commit_started=False;self.save_state()
        request={"action":action,"target":self.target_kind,"exe":self.game.text(),"backup_root":self.backup.text(),**extra}
        self.job=self.make_process(request);p=self.job;p.action=action;p.source=self.game.text()
        self.set_busy(True);self.status.setText(action.capitalize()+"…")
        p.readyReadStandardOutput.connect(lambda:self.read_messages(p,action));p.finished.connect(lambda code,status:self.job_finished(p,code))
        p.errorOccurred.connect(lambda error:self.process_error(p,error));p.start()

    def process_error(self,p,error):
        if error==C.QProcess.ProcessError.FailedToStart:
            self.fail("Worker failed to start: "+p.errorString());self.job_finished(p,1)

    def read_messages(self,p,action):
        p.readAllStandardOutput()  # Drain the optional console stream; file IPC is authoritative.
        try:
            with p.events_file.open("rb") as stream:
                stream.seek(p.event_offset);new=stream.read();p.event_offset=stream.tell()
        except OSError:return
        p.buffer+=new
        while b"\n" in p.buffer:
            line,p.buffer=p.buffer.split(b"\n",1)
            try:message=json.loads(line)
            except ValueError:continue
            kind=message.get("kind")
            if kind=="progress":
                text=message["message"];logging.info(text);self.activity.setText(text)
                if text.startswith("COMMIT:"):self.commit_started=True;self.cancel.setEnabled(False)
            elif kind=="error":
                p.received=True;error_text=message["message"]
                logging.error(message.get("details",error_text))
                if action=="analyze":
                    product="tModLoader" if self.target_kind=="tmodloader" else "Terraria"
                    state="Restore required" if "Restore a clean backup" in error_text else "Unavailable"
                    self.version_label.setText(f"{product} Version: {state}")
                    self.analysis_text.setText(error_text);self.analysis_text.show()
                self.fail(error_text)
                if message.get("permission"):self.admin.show()
            elif kind=="result":
                p.received=True;self.handle_result(action,message["result"],p)

    def handle_result(self,action,result,p):
        if action=="locate":
            if self.manual_path:return
            self.target_paths.update({k:v for k,v in result.items() if v})
            selected=result.get(self.target_kind)
            if selected:
                self.game.setText(selected);logging.info("%s detected: %s",self.target.currentText(),selected)
                C.QTimer.singleShot(0,lambda:self.start_job("analyze"))
            return
        if p.source!=self.game.text():return
        if action in ("analyze","patch"):
            self.result=result;old=self.state.get("last_patched_sha")
            product="tModLoader" if self.target_kind=="tmodloader" else "Terraria"
            info=f"{product} {result['version']} • {result['state']} • Render cap {result['cap']:,}\nSHA-256: {result['sha256']}"
            if self.target_kind=="tmodloader":info+="\nClient-side bridge; Steam launches apply the patch. Centered UI covers vanilla and mod-added interface layers."
            if action=="analyze" and old and old!=result["sha256"]:info+="\nExecutable changed since the last patch (update, verification, restore, or another tool). Reapply only after analysis."
            self.analysis_text.setText(info);logging.info("Executable analysis completed: %s",result["state"])
            version=str(result.get("version","Unknown"));latest=(self.target_kind=="terraria" and version=="1.4.5.8")
            tag=f' <b><span style="color:{color_theme.ui_color("button_background")}">(Latest)</span></b>' if latest else ""
            self.version_label.setText(f"{product} Version: {version}{tag}")
            if action=="patch":
                self.state["last_patched_sha"]=result["sha256"];self.state["last_backup"]=result["backup"];self.save_state()
                if self.target_kind=="tmodloader":self.activity.setText("Patch verified. Backup: "+result["backup"])
                else:self.activity.setText(("Clean backup created: " if result.get("clean_backup_created") else "Clean backup reused: ")+result["backup"])
                if self.launch_after:self.launch_game()
        elif action=="backups":p.backup_records=result["backups"]
        elif action=="restore":
            self.result=None
            self.activity.setText("tModLoader configuration restored and verified." if self.target_kind=="tmodloader" else "Clean Terraria backup restored and hash verified.")
            logging.info(self.activity.text())
        elif action=="launch_check":self.launch_game()
        self.status.setText("Verified" if action in ("patch","restore") else "Ready")

    def job_finished(self,p,code):
        if p is not self.job:return
        self.read_messages(p,p.action)
        if code and not p.received:
            logging.warning("Operation interrupted. A verified replacement may already have completed; inspect backups before retrying.")
            self.activity.setText("Operation interrupted. Verified backups remain available.")
        self.job=None;self.busy=False;self.set_busy(False)
        if p.guard:p.guard.close()
        if hasattr(p,"backup_records"):C.QTimer.singleShot(0,lambda records=p.backup_records:self.backup_dialog(records))
        p.watcher.removePaths(p.watcher.files());p.request_file.unlink(missing_ok=True);p.events_file.unlink(missing_ok=True);p.deleteLater()

    def set_busy(self,busy):
        for widget in (self.patch_play,self.patch_only,self.restore_button,self.launch,self.game,self.game_browse,self.game_open,self.backup,self.backup_browse,self.backup_open,self.desktop,self.target):widget.setEnabled(not busy)
        self.tabs.widget(0).setEnabled(not busy);self.tabs.widget(1).setEnabled(not busy)
        self.cancel.setVisible(busy);self.cancel.setEnabled(busy);self.tab_actions()

    def cancel_job(self):
        if self.job and not self.commit_started:
            if self.job.guard:self.job.guard.close()
            self.job.kill();self.status.setText("Interrupted")

    def begin_patch(self,play):
        if not self.require_backup():
            if play:self.record_nessa_click()
            return
        try:settings=self.settings()
        except ValueError as e:self.fail(str(e));return
        warnings=resolution_warnings(settings["Width"],settings["Height"])
        if settings["Mode"]==2:warnings.append("Exclusive fullscreen only works with modes exposed by the display driver. Borderless is recommended for independent-monitor spans.")
        if len(self.selected_display_names)==2:warnings.append("Two displays are selected. A bezel will cross the center of the game image.")
        if warnings and W.QMessageBox.warning(self,"Unusual settings","\n\n".join(warnings)+"\n\nKeep these exact settings and attempt the patch?",
                W.QMessageBox.StandardButton.Yes|W.QMessageBox.StandardButton.Cancel,W.QMessageBox.StandardButton.Cancel)!=W.QMessageBox.StandardButton.Yes:return
        self.launch_after=play;self.start_job("patch",settings=settings)

    def begin_patch_target(self,target):
        if self.busy:return
        if self.target_kind!=target:
            self.target_paths[self.target_kind]=self.game.text()
            self.target_kind=target
            self.target.blockSignals(True);self.target.setCurrentIndex(self.target.findData(target));self.target.blockSignals(False)
            self.game.blockSignals(True);self.game.setText(self.target_paths.get(target,""));self.game.blockSignals(False)
            self.manual_path=False;self.result=None;self.update_target_ui()
        self.begin_patch(True)

    def begin_launch(self):
        if self.require_backup():self.start_job("launch_check")

    def launch_game(self):
        app_id="1281930" if self.target_kind=="tmodloader" else "105600"
        ok=G.QDesktopServices.openUrl(C.QUrl("steam://rungameid/"+app_id))
        name="tModLoader" if self.target_kind=="tmodloader" else "Terraria"
        if not ok:self.fail("Windows could not launch "+name+". The verified backup is retained.");return
        logging.info("%s launch requested through Steam.",name);self.activity.setText(name+" launch requested through Steam. Backup retained.")

    def choose_restore(self):
        if self.require_backup():self.start_job("backups")

    def backup_dialog(self,records):
        if not records:self.fail("No backups for this installation at the selected location.");return
        dialog=W.QDialog(self);dialog.setWindowTitle("Restore a verified backup");dialog.resize(820,380);layout=W.QVBoxLayout(dialog)
        explanation=("Choose a verified tModLoader configuration backup. Worlds and players are never modified."
                     if self.target_kind=="tmodloader" else
                     "Choose a verified clean executable backup. Patched executables are never saved as restore points. Saves and worlds are never modified.")
        label=W.QLabel(explanation);label.setWordWrap(True);layout.addWidget(label)
        records=sorted(records,key=lambda r:r.get("created",""),reverse=True)
        table=W.QTableWidget(len(records),5);table.setHorizontalHeaderLabels(["Status","Created (UTC)","Saved state","Version","SHA-256"])
        for row,r in enumerate(records):
            status=W.QTableWidgetItem("Latest" if row==0 else "");status.setTextAlignment(C.Qt.AlignmentFlag.AlignCenter)
            if row==0:
                font=status.font();font.setBold(True);status.setFont(font);status.setForeground(G.QColor(color_theme.ui_color("button_background")))
            table.setItem(row,0,status)
            for col,key in enumerate(("created","original_state","original_version","original_sha256"),1):table.setItem(row,col,W.QTableWidgetItem(str(r.get(key,""))))
        table.setSelectionBehavior(W.QAbstractItemView.SelectionBehavior.SelectRows);table.setSelectionMode(W.QAbstractItemView.SelectionMode.SingleSelection)
        table.setEditTriggers(W.QAbstractItemView.EditTrigger.NoEditTriggers);table.horizontalHeader().setSectionResizeMode(W.QHeaderView.ResizeMode.Stretch);table.selectRow(0);layout.addWidget(table)
        buttons=W.QDialogButtonBox(W.QDialogButtonBox.StandardButton.Ok|W.QDialogButtonBox.StandardButton.Cancel);buttons.button(W.QDialogButtonBox.StandardButton.Ok).setText("Verify & restore")
        buttons.accepted.connect(dialog.accept);buttons.rejected.connect(dialog.reject);layout.addWidget(buttons)
        if dialog.exec()==W.QDialog.DialogCode.Accepted and table.currentRow()>=0:self.start_job("restore",selected=records[table.currentRow()]["folder"])

    def tab_actions(self):
        if not hasattr(self,"patch_play"):return
        name=self.tabs.tabText(self.tabs.currentIndex());main=name in ("Patch","Displays")
        for button in (self.patch_play,self.patch_only,self.restore_button,self.launch):button.setVisible(main)
        self.print_log.setVisible(name=="Log");self.copy_log.setVisible(name=="Log");self.show_all.setVisible(name=="Options")

    def save_state(self):
        state=load_app_state();state.update(self.state);self.target_paths[self.target_kind]=self.game.text();state["exe"]=self.game.text();state["target"]=self.target_kind;state["target_paths"]=self.target_paths;state["backup_root"]=self.backup.text();state["hide_log"]=getattr(self,"hide_log",None).isChecked() if hasattr(self,"hide_log") else False
        state["diagnostics_mode"]=self.diagnostics_mode.isChecked();state["prevent_minimize"]=self.prevent_minimize.isChecked()
        try:state["settings"]=self.settings()
        except ValueError:pass
        # Preserve theme state updated independently in the Options tab.
        fresh=load_app_state()
        if "custom_colors" in fresh:state["custom_colors"]=fresh["custom_colors"]
        else:state.pop("custom_colors",None)
        save_app_state(state);self.state=state

    def print_session(self):
        if not self.require_backup():return
        path=Path(self.backup.text())/(APP_NAME+" log - "+datetime.now().strftime("%Y-%m-%d %H-%M-%S")+".txt")
        path.write_text("\n".join(self.session_handler.lines)+"\n",encoding="utf-8");logging.info("Verbose log printed: %s",path);self.status.setText("Log exported")

    def fail(self,message):
        logging.error(message);self.status.setText("Attention required");W.QMessageBox.warning(self,WINDOW_TITLE,message)

    def record_nessa_click(self):
        now=time.monotonic();self.nessa_clicks=[t for t in self.nessa_clicks if now-t<1.35];self.nessa_clicks.append(now)
        if self.nessa_target<=0 and len(self.nessa_clicks)<4:return
        self.nessa_target=min(1.0,self.nessa_target+0.045)
        self.nessa_animation.stop();self.nessa_animation.setStartValue(self.nessa.getProgress());self.nessa_animation.setEndValue(self.nessa_target);self.nessa_animation.start()

    def set_log_hidden(self,hidden):
        if not hasattr(self,"tabs") or not hasattr(self,"log_page"):return
        index=self.tabs.indexOf(self.log_page)
        if index>=0:
            if hidden and self.tabs.currentWidget()==self.log_page:self.tabs.setCurrentIndex(0)
            self.tabs.setTabVisible(index,not hidden)

    def resizeEvent(self,event):
        super().resizeEvent(event)
        if hasattr(self,"nessa") and self.nessa.getProgress()>0:self.nessa.reposition()

    def restart_admin(self):
        self.save_state();cmd=self.command();arguments=subprocess.list2cmdline(cmd[1:]);shell=ctypes.windll.shell32
        shell.ShellExecuteW.restype=ctypes.c_void_p
        result=shell.ShellExecuteW(None,"runas",cmd[0],arguments,str(Path(cmd[-1]).parent),1)
        if result and result>32:self.close()
        else:self.fail("Administrator restart was cancelled or failed. Game left unchanged.")

    def closeEvent(self,event):
        self.save_state();self.stop_discovery()
        if self.job:
            if self.job.guard:self.job.guard.close()
            self.job.kill();self.job.waitForFinished(1500)
        if self.discovery:self.discovery.waitForFinished(1500)
        logging.getLogger().removeHandler(self.log_handler);logging.getLogger().removeHandler(self.session_handler)
        event.accept()
