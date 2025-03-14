# ShotBoard
# By Jean-Yves 'madjyc' Chasle
# SPDX-License-Identifier: MIT
# ShotBoard: Visualize movies shot by shot

# Research
# https://python.hotexamples.com/fr/examples/PyQt5.QtMultimedia/QMediaPlayer/setMedia/python-qmediaplayer-setmedia-method-examples.html
# https://stackoverflow.com/questions/52359924/pyqt5-access-frames-with-qmediaplayer
# https://stackoverflow.com/questions/27006902/force-qmediaplayer-to-update-position-accurately-for-video-scrubbing-application
# https://stackoverflow.com/questions/57889211/pyqt-qmediaplayer-setposition-rounds-the-position-value

from shotboard_db import *
from shotboard_ui import *
from shotboard_cmd import *
from shotboard_med import *

import subprocess
import cv2
import numpy as np
from skimage.metrics import structural_similarity as ssim
import matplotlib.pyplot as plt
import os
import sys
import datetime
import math
from functools import wraps
from inspect import signature
from PyQt5.QtCore import Qt, pyqtSignal, QRect, QTime, QElapsedTimer
from PyQt5.QtWidgets import QApplication, QMainWindow, QWidget, QMessageBox, QDialog, QFileDialog, QProgressDialog
from PyQt5.QtWidgets import QSplitter, QHBoxLayout, QVBoxLayout, QGridLayout, QScrollArea, QSlider, QSpinBox
from PyQt5.QtWidgets import QLabel, QPushButton, QToolButton, QCheckBox
from PyQt5.QtWidgets import QAction, QStyle
from PyQt5.QtGui import QKeySequence, QColor, QPalette


APP_VERSION = "0.9.3"

# Main UI
DEFAULT_GEOMETRY = QRect(0, 0, 1280, 720)
DEFAULT_TITLE = "𝗦𝗵𝗼𝘁𝗯𝗼𝗮𝗿𝗱"

# SSIM shot detection
SIM_DROP_THRESHOLD_MIN = 0.05
SIM_DROP_THRESHOLD_MAX = 0.30
SIM_DROP_THRESHOLD_DEFAULT = 0.10

# Detection slider
DETECTION_SLIDER_STEPS = int((SIM_DROP_THRESHOLD_MAX - SIM_DROP_THRESHOLD_MIN) / 0.01)
DEFAULT_DETECTION_SLIDER_VALUE = int(((SIM_DROP_THRESHOLD_DEFAULT - SIM_DROP_THRESHOLD_MIN) / (SIM_DROP_THRESHOLD_MAX - SIM_DROP_THRESHOLD_MIN)) * DETECTION_SLIDER_STEPS)

# UI colors
VIDEO_BACKGROUND_COLOR = "#000000"  # Black
BOARD_BACKGROUND_COLOR = "#2e2e2e"  # Dark gray

# Timestamp in seconds vs milliseconds
SLIDER_TIMESTAMP_MILLISECONDS = False

DEFAULT_FFMPEG_FRAME_SEEK_OFFSET = -1  # Slight frame offset to prevent FFmpeg from rounding to nearest (previous) frame

# Debug
LOG_FUNCTION_NAMES = False
PRINT_DEFAULT_COLOR = '\033[0m'
PRINT_GRAY_COLOR = '\033[90m'
PRINT_RED_COLOR = '\033[91m'  # red
PRINT_GREEN_COLOR = '\033[92m'  # green
PRINT_YELLOW_COLOR  = '\033[93m'  # yellow
PRINT_CYAN_COLOR = '\033[96m'  # cyan

# Use save extra-shortcut - or not
RIGHT_CLICK_SAVE = True


##
## DECORATORS
##


def log_function_name(color=PRINT_DEFAULT_COLOR):
    def decorator(func):
        @wraps(func)
        def wrapper(self, *args, **kwargs):
            if LOG_FUNCTION_NAMES:
                class_name = self.__class__.__name__
                function_name = func.__name__
                print(f"Calling function: {class_name}.{color}{function_name}{PRINT_DEFAULT_COLOR}")

            # Check if the function has parameters (other than 'self')
            sig = signature(func)
            has_params = len(sig.parameters) > 1  # 'self' is the first parameter

            # Call the function with or without arguments based on its signature
            return func(self, *args, **kwargs) if has_params else func(self)
        return wrapper
    return decorator


# Undo/redo command wrapper (selection context)
def command_selection_context(func):
    @wraps(func)
    def wrapper(self, *args, **kwargs):
        # Store context before action execution
        old_first_index, old_last_index = self._selection_first_index, self._selection_last_index

        # Execute action whether or not the function accepts arguments
        sig = signature(func)
        if len(sig.parameters) > 1:  # 'self' is the first parameter
            result = func(self, *args, **kwargs)
        else:
            result = func(self)

        if result is None:
            return  # Stop execution if action was unsuccessful

        # Store context after action execution
        new_first_index, new_last_index = self._selection_first_index, self._selection_last_index

        # Push undo/redo command in history
        cmd = Command()
        cmd.set_undo(func=self.restore_selection, data={'first_index': old_first_index, 'last_index': old_last_index})
        cmd.set_redo(func=self.restore_selection, data={'first_index': new_first_index, 'last_index': new_last_index})
        self._history.push(cmd)

    return wrapper


# Undo/redo command wrapper (full context)
def command_full_context(func):
    @wraps(func)
    def wrapper(self, *args, **kwargs):
        # Store context before action execution
        old_frame_indexes = self._db.get_shots()
        old_first_index, old_last_index = self._selection_first_index, self._selection_last_index

        # Execute action whether or not the function accepts arguments
        sig = signature(func)
        if len(sig.parameters) > 1:  # 'self' is the first parameter
            result = func(self, *args, **kwargs)
        else:
            result = func(self)

        if result is None:
            return  # Stop execution if action was unsuccessful

        # Store context after action execution
        new_frame_indexes = self._db.get_shots()
        new_first_index, new_last_index = self._selection_first_index, self._selection_last_index

        # Push undo/redo command in history
        cmd = Command()
        cmd.set_undo(func=self.restore_context, data={'frame_indexes': old_frame_indexes, 'first_index': old_first_index, 'last_index': old_last_index})
        cmd.set_redo(func=self.restore_context, data={'frame_indexes': new_frame_indexes, 'first_index': new_first_index, 'last_index': new_last_index})
        self._history.push(cmd)

        return result
    return wrapper


##
##  MAIN WINDOW
##


class ShotBoard(QMainWindow):
    class ClickableScrollArea(QScrollArea):
        clicked = pyqtSignal()  # Signal to emit when the scroll area is clicked

        def mousePressEvent(self, event):
            self.clicked.emit()
            # Call the base class implementation to ensure normal behavior
            super().mousePressEvent(event)


    @log_function_name(color=PRINT_GREEN_COLOR)
    def __init__(self, geom=DEFAULT_GEOMETRY):
        super().__init__()

        self._db = ShotBoardDb()
        self._db_path = None
        self._history = CommandHistory()
        self._shot_widget_mgr = ShotWidgetManager()
        self._shot_widget_mgr.thumbnail_loaded.connect(self.on_thumbnail_loaded)
        self._shot_widget_mgr.hovered.connect(self.on_shot_widget_hovered)
        self._shot_widget_mgr.clicked.connect(self.on_shot_widget_clicked)

        self._selection_first_index = None
        self._selection_last_index = None
        self._video_info = VideoInfo()
        self._ui_enabled = True
        self._hovered_shot_widget = None
        self._old_zoom_value = DEFAULT_SHOT_IMAGE_SIZE_INDEX

        self._clock_emojis = ["🕛", "🕐", "🕑", "🕒", "🕓", "🕔", "🕕", "🕖", "🕗", "🕘", "🕙", "🕚"]
        self._loading_step = 0  # clock_emojis

        self.update_window_title()
        self.setGeometry(geom)
        self.setMinimumSize(1024, 768)

        self.create_menu()

        # Create the top and bottom widgets
        top_widget = self.create_top_widget()
        bottom_widget = self.create_bottom_widget()

        # Create a central widget to hold the splitter
        central_widget = QWidget()
        central_layout = QVBoxLayout(central_widget)
        central_layout.setContentsMargins(10, 0, 10, 0)

        # Create a QSplitter to host the top and bottom widgets
        splitter = QSplitter(Qt.Vertical)
        splitter.splitterMoved.connect(self.on_handle_splitter_moved)
        splitter.addWidget(top_widget)
        splitter.addWidget(bottom_widget)
        splitter.setSizes([geom.height() // 2, geom.height() // 2])
        splitter.setHandleWidth(0)

        # Add the splitter to the central layout
        central_layout.addWidget(splitter)

        # Set the central widget
        self.setCentralWidget(central_widget)

        QApplication.instance().installEventFilter(self)  # Install global event filter

        self.create_status_bar()
        self.statusBar().showMessage("Load a video.")
        self.update_ui_state()

        # TODO: Not a satisfying solution yet
        self._resize_timer = QTimer(self)
        self._resize_timer.setSingleShot(True)
        self._resize_timer.setInterval(500)
        self._resize_timer.timeout.connect(self.delayed_update)


    ##
    ## MENU, STATUS BAR
    ##


    @log_function_name()
    def create_status_bar(self):
        # Get the default status bar
        self._status_bar = self.statusBar()

        # Add another QLabel aligned to the bottom right
        self._info_label = QLabel()
        self._status_bar.addPermanentWidget(self._info_label)  # Aligns to the right
        self.update_status_bar()


    @log_function_name()
    def create_menu(self):
        # Create a menu bar
        menubar = self.menuBar()

        #
        # Create a 'File' menu
        #
        file_menu = menubar.addMenu('File')

        # Create 'New' action
        action = QAction('New', self)
        action.triggered.connect(self.on_menu_new)
        action.setShortcut(QKeySequence.New)
        file_menu.addAction(action)

        file_menu.addSeparator()

        # Create 'Open' action
        action = QAction('Open Video', self)
        action.triggered.connect(self.on_menu_open_video)
        action.setShortcut(QKeySequence.Open)
        file_menu.addAction(action)

        # Create 'Open' action
        action = QAction('Open Shot List', self)
        action.triggered.connect(self.on_menu_open_shotlist)
        action.setShortcut(QKeySequence(Qt.SHIFT + Qt.CTRL + Qt.Key_O))
        file_menu.addAction(action)

        file_menu.addSeparator()

        # Create 'Save' action
        action = QAction('Save', self)
        action.triggered.connect(self.on_menu_save)
        action.setShortcut(QKeySequence.Save)
        file_menu.addAction(action)

        # Create 'Save As' action
        action = QAction('Save as', self)
        action.triggered.connect(self.on_menu_save_as)
        action.setShortcut(QKeySequence(Qt.SHIFT + Qt.CTRL + Qt.Key_S))
        file_menu.addAction(action)

        file_menu.addSeparator()

        # Create 'Export...' submenu
        export_menu = file_menu.addMenu('Export...')

        # Create 'Export Selection' action
        action = QAction('Export Selection', self)
        action.triggered.connect(self.on_menu_export_selection)
        action.setShortcut(QKeySequence(Qt.CTRL + Qt.Key_E))
        export_menu.addAction(action)

        # Create 'Export Frame' action
        action = QAction('Export Frame', self)
        action.triggered.connect(self.on_menu_export_current_frame)
        action.setShortcut(QKeySequence(Qt.ALT + Qt.Key_E))
        export_menu.addAction(action)

        # Create 'Export As...' submenu
        export_as_menu = file_menu.addMenu('Export As...')

        # Create 'Export Selection As' action
        action = QAction('Export Selection As', self)
        action.triggered.connect(self.on_menu_export_selection_as)
        action.setShortcut(QKeySequence(Qt.SHIFT + Qt.CTRL + Qt.Key_E))
        export_as_menu.addAction(action)

        # Create 'Export Frame As' action
        action = QAction('Export Frame As', self)
        action.triggered.connect(self.on_menu_export_current_frame_as)
        action.setShortcut(QKeySequence(Qt.SHIFT + Qt.ALT + Qt.Key_E))
        export_as_menu.addAction(action)

        file_menu.addSeparator()

        # Create 'Exit' action
        action = QAction('Exit', self)
        action.triggered.connect(self.on_menu_exit)
        action.setShortcut(QKeySequence(Qt.CTRL + Qt.Key_Q))
        file_menu.addAction(action)

        #
        # Create an 'Edit' menu
        #
        edit_menu = menubar.addMenu('Edit')
        
        # Create an "Undo" action with Ctrl + Z shortcut
        action = QAction("Undo", self)
        action.triggered.connect(self._history.undo)
        action.setShortcuts([QKeySequence.Undo])  # QKeySequence("Ctrl+Z")
        edit_menu.addAction(action)
        #action.setDisabled(True)

        # Create an "Redo" action with Ctrl + Z shortcut
        action = QAction("Redo", self)
        action.triggered.connect(self._history.redo)
        action.setShortcuts([QKeySequence.Redo])  # QKeySequence("Ctrl+Y"), QKeySequence("Shift+Ctrl+Z")
        edit_menu.addAction(action)
        #action.setDisabled(True)

        edit_menu.addSeparator()

        # Create an "Redo" action with Ctrl + Z shortcut
        action = QAction("Select All", self)
        action.triggered.connect(self.cmd_select_all)
        action.setShortcuts([QKeySequence.SelectAll])  # Ctrl+A
        edit_menu.addAction(action)
        #action.setDisabled(True)

        # Create an "Redo" action with Ctrl + Z shortcut
        action = QAction("Deselect All", self)
        action.triggered.connect(self.cmd_deselect_all)
        action.setShortcuts([QKeySequence("Ctrl+D")])
        edit_menu.addAction(action)
        #action.setDisabled(True)


    ##
    ## MAIN WIDGETS
    ##
    

    @log_function_name()
    def create_top_widget(self):
        top_widget = QWidget()

        # Create a vertical layout for the top part
        top_layout = QVBoxLayout()
        top_widget.setLayout(top_layout)
        margins = top_layout.contentsMargins()
        top_layout.setContentsMargins(0, margins.top(), 0, margins.bottom())

        # Create a media player object
        self._mediaplayer = SBMediaPlayer()
        self._mediaplayer.stateChanged.connect(self.on_mediaplayer_state_changed)
        # self._mediaplayer.error.connect(self.on_mediaplayer_error)
        self._mediaplayer.frameChanged.connect(self.on_mediaplayer_frame_changed)
        #self._mediaplayer.durationChanged.connect(self.on_mediaplayer_duration_changed)
        self._mediaplayer.clicked.connect(self.on_video_clicked)
        top_layout.addWidget(self._mediaplayer)

        # Create a horizontal layout for the slider
        slider_layout = QHBoxLayout()
        top_layout.addLayout(slider_layout)

        # Timestamp label
        timestamp = "0:00:00:000" if SLIDER_TIMESTAMP_MILLISECONDS else "0:00:00"
        self._timestamp_label = QLabel(timestamp)
        self._timestamp_label.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        slider_layout.addWidget(self._timestamp_label)

        self._seek_slider = QSlider(Qt.Horizontal)
        self._seek_slider.setRange(0, 0)
        self._seek_slider.mousePressEvent = self.on_seek_slider_click
        self._seek_slider.sliderMoved.connect(self.on_seek_slider_moved)
        slider_layout.addWidget(self._seek_slider)

        # Create a spinbox
        self._seek_spinbox = QSpinBox()
        self._seek_spinbox.valueChanged.connect(self.on_seek_spinbox_changed)
        self._seek_spinbox.setStatusTip("Directly set the position.")
        slider_layout.addWidget(self._seek_spinbox)

        # Create a horizontal layout for the buttons
        button_layout = QHBoxLayout()
        top_layout.addLayout(button_layout)

        # Create a play button
        self._play_button = QToolButton()
        self._play_button.setIcon(self.style().standardIcon(QStyle.SP_MediaPlay))
        self._play_button.clicked.connect(self.on_play_button_clicked)
        self._play_button.setStatusTip("Click to start playing.")
        button_layout.addWidget(self._play_button)

        # Create a stop button
        self._stop_button = QToolButton()
        self._stop_button.setIcon(self.style().standardIcon(QStyle.SP_MediaStop))
        self._stop_button.clicked.connect(self.on_stop_button_clicked)
        self._stop_button.setStatusTip("Click to stop playing.")
        button_layout.addWidget(self._stop_button)

        # Create a skip backward button
        self._skip_bwd_button = QToolButton()
        self._skip_bwd_button.setIcon(self.style().standardIcon(QStyle.SP_MediaSkipBackward))
        self._skip_bwd_button.clicked.connect(self.on_skip_bwd_clicked)
        self._skip_bwd_button.setStatusTip("Skip to pprevious shot.")
        button_layout.addWidget(self._skip_bwd_button)

        # Create a skip foreward button
        self._skip_fwd_button = QToolButton()
        self._skip_fwd_button.setIcon(self.style().standardIcon(QStyle.SP_MediaSkipForward))
        self._skip_fwd_button.clicked.connect(self.on_skip_fwd_clicked)
        self._skip_fwd_button.setStatusTip("Skip to next shot.")
        button_layout.addWidget(self._skip_fwd_button)

        button_layout.addStretch()

        # Create an edge detection checkbox with a label
        self._edgedetect_checkbox = QCheckBox("Lines")
        self._edgedetect_checkbox.toggled.connect(self.on_edge_detection_toggled)
        self._edgedetect_checkbox.setChecked(False)
        self._edgedetect_checkbox.setStatusTip("Check to apply 'Sobel' edge detection to the thumbnails.")
        SBMediaPlayer.detect_edges = False
        ShotWidget.detect_edges = False
        button_layout.addWidget(self._edgedetect_checkbox)

        # Create an edge factor spinbox
        self._edgefactor_spinbox = QSpinBox()
        self._edgefactor_spinbox.setRange(1, 10)
        self._edgefactor_spinbox.valueChanged.connect(self.on_edge_factor_changed)
        self._edgefactor_spinbox.setValue(1)
        self._edgefactor_spinbox.setStatusTip("Set the contrast factor of the 'Sobel' edge detection algorhythm.")
        button_layout.addWidget(self._edgefactor_spinbox)

        # Create a split button
        self._split_button = QPushButton('Mark current frame as new shot')
        self._split_button.clicked.connect(self.on_split_button_clicked)
        self._split_button.setStyleSheet(f"background-color: {SHOT_WIDGET_PROGRESSBAR_COLOR};")
        self._split_button.setStatusTip("Add a new shot to the list starting at current position (in case it has not already been detected as the beginning of a shot).")
        button_layout.addStretch()
        button_layout.addWidget(self._split_button)

        # Create a scan button
        self._scan_button = QPushButton('Scan selected shots')
        self._scan_button.clicked.connect(self.on_scan_button_clicked)
        self._scan_button.setStyleSheet(f"background-color: {SHOT_WIDGET_RESCAN_COLOR};")
        self._scan_button.setStatusTip("Scan (or re-scan) the selected shots using the current similarity tolerance value.")

        # Create a plot checkbox with a label
        self._plot_checkbox = QCheckBox("Monitor")  
        self._plot_checkbox.setChecked(False)
        self._plot_checkbox.setStatusTip("Check to display a real-time plot of SSIM values during frame analysis. Close the graph when done.")

        # Seek offset label (DEBUG ONLY: NOT DISPLAYED)
        seek_offset_label = QLabel("Offset")
        seek_offset_label.setAlignment(Qt.AlignRight | Qt.AlignVCenter)

        # Create an seek offset spinbox (DEBUG ONLY: NOT DISPLAYED)
        self._seek_offset_spinbox = QSpinBox()
        self._seek_offset_spinbox.setRange(-10, 10)
        self._seek_offset_spinbox.setValue(DEFAULT_FFMPEG_FRAME_SEEK_OFFSET)
        self._seek_offset_spinbox.valueChanged.connect(self.on_seek_offset_spinbox_changed)
        self._seek_offset_spinbox.setStatusTip("Set a corrective offset (in 10th of a frame) to accurately seek shot start frames.")

        # Create a double condition checkbox with a label
        self._double_condition_checkbox = QCheckBox("Stabilized")  
        self._double_condition_checkbox.setChecked(True)
        self._double_condition_checkbox.setStatusTip("Check to detect shots with the frame similarity pattern: \"similar→different→similar\". Uncheck to simply use: \"similar→different\" (more error prone).")

        # Detection level slider
        self._detection_slider = QSlider(Qt.Horizontal)
        self._detection_slider.setRange(0, DETECTION_SLIDER_STEPS)
        self._detection_slider.setValue(DEFAULT_DETECTION_SLIDER_VALUE)
        #self._detection_slider.mousePressEvent = self.on_detection_slider_click
        self._detection_slider.sliderMoved.connect(self.on_detection_slider_moved)
        self._detection_slider.setFixedWidth(100)
        self._detection_slider.setStatusTip("Set the frame similarity threshold. Lower values require lesser difference between consecutive frames to detect a new shot.")

        # Detection label
        self._detection_label = QLabel(f"{self.convert_detection_slider_value_to_ssim_drop_threshold(self._detection_slider.value()):.2f}")
        self._detection_label.setAlignment(Qt.AlignRight | Qt.AlignVCenter)

        # Create a downscale spinbox (DEBUG ONLY: NOT DISPLAYED)
        self._downscale_spinbox = QSpinBox()
        self._downscale_spinbox.setRange(64, 1280)
        self._downscale_spinbox.setSingleStep(64)
        self._downscale_spinbox.setValue(128)
        self._downscale_spinbox.setStatusTip("Set the width of the downscaled image size to ease shot detection.")

        # Create a layout for the detection widgets
        detection_layout = QHBoxLayout()
        detection_layout.addStretch()  # Add stretch to push elements to the right
        detection_layout.addWidget(self._scan_button)
        detection_layout.addWidget(self._plot_checkbox)
        # detection_layout.addWidget(seek_offset_label)
        # detection_layout.addWidget(self._seek_offset_spinbox)
        detection_layout.addWidget(self._double_condition_checkbox)
        detection_layout.addWidget(self._detection_slider)
        detection_layout.addWidget(self._detection_label)
        # detection_layout.addWidget(self._downscale_spinbox)
        detection_layout.setSpacing(5)  # Adjust spacing between label and slider

        button_layout.addStretch()
        button_layout.addLayout(detection_layout)

        # Create a shift backward button
        self._shift_backward_button = QPushButton('➕')
        self._shift_backward_button.setFixedWidth(24)
        self._shift_backward_button.clicked.connect(self.on_shift_backward_button_clicked)
        # self._shift_backward_button.setStyleSheet(f"background-color: {SHOT_WIDGET_SELECT_COLOR};")
        self._shift_backward_button.setStatusTip("Shift the start of the selected shot backward by one frame.")
        button_layout.addStretch()
        button_layout.addWidget(self._shift_backward_button)

        # Create a shift foreward button
        self._shift_foreward_button = QPushButton('➖')
        self._shift_foreward_button.setFixedWidth(24)
        self._shift_foreward_button.clicked.connect(self.on_shift_foreward_button_clicked)
        # self._shift_foreward_button.setStyleSheet(f"background-color: {SHOT_WIDGET_SELECT_COLOR};")
        self._shift_foreward_button.setStatusTip("Shift the start of the selected shot foreward by one frame.")
        button_layout.addWidget(self._shift_foreward_button)

        # Create a merge button
        self._merge_button = QPushButton('Merge selected shots')
        self._merge_button.clicked.connect(self.on_merge_button_clicked)
        self._merge_button.setStyleSheet(f"background-color: {SHOT_WIDGET_SELECT_COLOR};")
        self._merge_button.setStatusTip("Merge the selected shots as one shot (in case they were incorrectly detected as separate shots).")
        button_layout.addWidget(self._merge_button)

        # Create a DEBUG button
        # self._debug_button = QPushButton('DEBUG')
        # self._debug_button.clicked.connect(self.on_debug_button_clicked)
        # button_layout.addStretch()
        # button_layout.addWidget(self._debug_button)

        # Zoom label
        zoom_label = QLabel("Zoom")
        zoom_label.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        button_layout.addStretch()
        button_layout.addWidget(zoom_label)

        # Create an zoom spinbox
        self._zoom_spinbox = QSpinBox()
        self._zoom_spinbox.setRange(SHOT_IMAGE_SIZES_MIN, SHOT_IMAGE_SIZES_MAX)
        self._zoom_spinbox.setValue(DEFAULT_SHOT_IMAGE_SIZE_INDEX)
        self._zoom_spinbox.valueChanged.connect(self.on_zoom_changed)
        self._zoom_spinbox.setStatusTip("Set the number of images in a row.")
        button_layout.addWidget(self._zoom_spinbox)

        # Create a skip backward button
        self._speaker_button = QToolButton()
        self._speaker_button.setIcon(self.style().standardIcon(QStyle.SP_MediaVolume))
        self._speaker_button.setCheckable(True)
        self._speaker_button.setChecked(False)
        self._speaker_button.clicked.connect(self.on_speaker_btn_toggled)
        self._speaker_button.setStatusTip("Mute/unmute sound.")

        # Volume slider
        self._volume_slider = QSlider(Qt.Horizontal)
        self._volume_slider.setRange(0, int(MAX_VOLUME_FACTOR * 100))
        self._volume_slider.setValue(75)
        self._volume_slider.mousePressEvent = self.on_volume_slider_click
        self._volume_slider.sliderMoved.connect(self.on_volume_slider_moved)
        self._volume_slider.setFixedWidth(150)
        self._volume_slider.setStatusTip("Set the volume of the video.")
        ShotWidget.volume = self._volume_slider.value() * 0.01

        # Volume label
        self._volume_label = QLabel(f"{self._volume_slider.value():3}%")
        self._volume_label.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        self._volume_label.setFixedWidth(28)

        # Create a layout for the volume slider and label
        volume_layout = QHBoxLayout()
        volume_layout.addStretch()
        volume_layout.addWidget(self._speaker_button)
        volume_layout.addWidget(self._volume_slider)
        volume_layout.addWidget(self._volume_label)
        volume_layout.setSpacing(5)  # Adjust spacing between label and slider

        button_layout.addStretch()
        button_layout.addLayout(volume_layout)

        return top_widget


    @log_function_name()
    def create_bottom_widget(self):
        bottom_widget = QWidget()

        bottom_layout = QVBoxLayout()
        bottom_widget.setLayout(bottom_layout)
        margins = bottom_layout.contentsMargins()
        bottom_layout.setContentsMargins(0, 0, 0, margins.bottom())

        scrollarea_layout = QHBoxLayout()
        bottom_layout.addLayout(scrollarea_layout)

        # Wrap the grid layout in a scroll area
        self._scroll_area = self.ClickableScrollArea()
        self._scroll_area.setWidgetResizable(True)
        self._scroll_area.setStyleSheet(f"background-color: {BOARD_BACKGROUND_COLOR};")
        self._scroll_area.clicked.connect(self.cmd_deselect_all)
        self._scroll_area.verticalScrollBar().valueChanged.connect(self.on_scroll)
        self._scroll_area.setStatusTip("Click on a shot to play.")
        scrollarea_layout.addWidget(self._scroll_area)

        # Create a container widget to hold the grid layout
        grid_widget = QWidget()
        self._grid_layout = QGridLayout()
        self._grid_layout.setAlignment(Qt.AlignTop | Qt.AlignLeft)
        self._grid_layout.setSpacing(GRID_LAYOUT_SPACING)
        grid_widget.setLayout(self._grid_layout)
        self._scroll_area.setWidget(grid_widget)

        return bottom_widget


    ##
    ## SLOTS
    ##


    @log_function_name(color=PRINT_GREEN_COLOR)
    def on_menu_new(self):
        self.reset_all()


    @log_function_name(color=PRINT_GREEN_COLOR)
    def on_menu_open_video(self):
        self.ask_to_save_if_dirty()

        file_dialog = QFileDialog()
        file_dialog.setAcceptMode(QFileDialog.AcceptOpen)
        file_dialog.setNameFilter("Video files (*.mp4 *.avi *.mkv)")
        if file_dialog.exec_() == QFileDialog.Accepted:
            url = file_dialog.selectedUrls()[0]
            self.set_video(url)

            # Check for a matching JSON file
            if self._video_info.video_path:
                json_path = os.path.splitext(self._video_info.video_path)[0] + ".json"
                if os.path.exists(json_path):
                    json_filename = os.path.basename(json_path)  # Extract filename only
                    reply = QMessageBox.question(
                        self,
                        "Load Shotlist?",
                        f"A matching shot list was found:\n{json_filename}\nDo you want to load it?",
                        QMessageBox.Yes | QMessageBox.No,
                        QMessageBox.Yes
                    )
                    if reply == QMessageBox.Yes:
                        self.open_shot_list(json_path)


    @log_function_name(color=PRINT_GREEN_COLOR)
    def on_menu_open_shotlist(self):
        if not self._video_info.video_path:
            QMessageBox.warning(self, "Warning", "Please load a video first.")
            return

        self.ask_to_save_if_dirty()

        options = QFileDialog.Options()
        json_path, _ = QFileDialog.getOpenFileName(self, "Open Shot File", None, "Shot Files (*.json);;All Files (*)", options=options)
        self.open_shot_list(json_path)


    @log_function_name(color=PRINT_GREEN_COLOR)
    def on_menu_save(self):
        if self._db_path:
            self.save_shot_list(self._db_path)
        else:
            self.on_menu_save_as()
        self.update_window_title()


    @log_function_name(color=PRINT_GREEN_COLOR)
    def on_menu_save_as(self):
        if self._db_path is None:
            # Generate a default filename based on the video file's name
            video_path = self._video_info.video_path
            if video_path:
                # Replace the video extension with .json
                base_name, _ = os.path.splitext(video_path)
                suggested_path = f"{base_name}.json"
            else:
                # Fallback if video_path is not available
                suggested_path = ""
        else:
            # Use self._db_path as the default filename
            suggested_path = self._db_path

        # Open the file dialog with the suggested filename
        options = QFileDialog.Options()
        path, _ = QFileDialog.getSaveFileName(
            self,
            "Save Shot File",
            suggested_path,  # Use the suggested filename
            "Shot Files (*.json);;All Files (*)",
            options=options
        )

        if path:
            # Remove the existing extension and enforce .json
            base_name, _ = os.path.splitext(video_path)
            path = f"{base_name}.json"

            # Warn the user if the file exists
            # if os.path.exists(path) and not QMessageBox.question(
            #     self,
            #     "File Exists",
            #     f"The file {os.path.basename(path)} already exists. Do you want to overwrite it?",
            #     QMessageBox.Yes | QMessageBox.No,
            #     QMessageBox.No
            # ) == QMessageBox.Yes:
            #     return

            self.save_shot_list(path)


    @log_function_name(color=PRINT_GREEN_COLOR)
    def on_menu_export_selection(self):
        self.export_selection(False)


    @log_function_name(color=PRINT_GREEN_COLOR)
    def on_menu_export_selection_as(self):
        self.export_selection(True)


    @log_function_name(color=PRINT_GREEN_COLOR)
    def on_menu_export_current_frame(self):
        self.export_single_frame(self._seek_spinbox.value(), False)


    @log_function_name(color=PRINT_GREEN_COLOR)
    def on_menu_export_current_frame_as(self):
        self.export_single_frame(self._seek_spinbox.value(), True)


    @log_function_name(color=PRINT_GREEN_COLOR)
    def on_menu_exit(self):
        reply = QMessageBox.question(self, 'Confirm Exit', 'Exit?', QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
        if reply == QMessageBox.Yes:
            self.close()


    @log_function_name(color=PRINT_GREEN_COLOR)
    def resizeEvent(self, event):
        self._resize_timer.start()
        # self._scroll_area.setUpdatesEnabled(False)
        super().resizeEvent(event)


    # @log_function_name(color=PRINT_GREEN_COLOR)
    def delayed_update(self):
        # self._scroll_area.setUpdatesEnabled(True)
        self.update_grid_layout()


    #@log_function_name()
    def eventFilter(self, obj, event):
        if self._ui_enabled:
            if event.type() == QEvent.MouseButtonPress:
                if event.button() == Qt.RightButton:
                    if RIGHT_CLICK_SAVE:
                        self.on_menu_save()
                        return True  # Event handled

            # elif event.type() == QEvent.Wheel:
            #     scroll_delta = event.angleDelta().y()
            #     volume_increment = 0.05  # Volume per scroll step
            #     if scroll_delta > 0:  # Scroll up (increase volume)
            #         self._volume = min(self._volume + volume_increment, 1.0)  # Cap volume at 1.0
            #     elif scroll_delta < 0:  # Scroll down (decrease volume)
            #         self._volume = max(self._volume - volume_increment, 0.0)  # Cap volume at 0.0
            #     self._volume_slider.setValue(self._volume)
            #     return True  # Event handled
            
            elif event.type() == QEvent.KeyPress:
                if event.key() == Qt.Key_Space:
                    if self._hovered_shot_widget:
                        self._hovered_shot_widget.stop_videoplayer()
                    self._play_button.click()  # Play / pause
                    return True  # Event handled

                modifiers = event.modifiers()
                if modifiers & Qt.ShiftModifier:
                    frame_inc = 4  # 4 frames
                elif modifiers & Qt.ControlModifier:
                    frame_inc = round(self._video_info.fps)  # 1 second
                elif modifiers & Qt.AltModifier:
                    frame_inc = round(4 * self._video_info.fps)  # 4 seconds
                else:
                    frame_inc = 1

                if self._mediaplayer.get_state() == SBMediaPlayer.PlayingState:
                    self.pause_video()
                frame_index = self._seek_spinbox.value()

                if event.key() == Qt.Key_Right:
                    self.seek_video(frame_index + frame_inc)
                    return True  # Event handled
                elif event.key() == Qt.Key_Left:
                    self.seek_video(frame_index - frame_inc)
                    return True  # Event handled

        # Unprocessed events propagate as usual
        return super().eventFilter(obj, event)


    @log_function_name(color=PRINT_GREEN_COLOR)
    def on_handle_splitter_moved(self, pos, index):
        pass


    @log_function_name(color=PRINT_GREEN_COLOR)
    def on_video_clicked(self):
        if not self._video_info.video_path or not self._ui_enabled:
            return
        self.on_play_button_clicked()


    @log_function_name(color=PRINT_GREEN_COLOR)
    def on_play_button_clicked(self):
        if not self._video_info.video_path:
            return

        player_state = self._mediaplayer.get_state()
        if player_state == SBMediaPlayer.StoppedState:
            self.play_video()
        elif player_state == SBMediaPlayer.PlayingState:
            self.pause_video()
        elif player_state == SBMediaPlayer.PausedState:
            self.resume_video()


    @log_function_name(color=PRINT_GREEN_COLOR)
    def on_stop_button_clicked(self):
        self.stop_video()


    @log_function_name(color=PRINT_GREEN_COLOR)
    def on_skip_bwd_clicked(self):
        self.pause_video()
        if len(self._shot_widget_mgr) == 0:
            return
        
        # Find which shot widget contains the current frame index.
        frame_index = self._seek_spinbox.value()
        start_frame_index, _ = self._db.get_start_end_frame_indexes(frame_index)

        # Skip to the beginning of that shot if frame_index is past the beginning.
        # Skip to the previous shot if frame_index is already at the beginning.
        if frame_index > start_frame_index:
            prev_shot_index = self._db.get_shot_index(start_frame_index)
        else:
            prev_shot_index = max(0, self._db.get_shot_index(start_frame_index) - 1)
        prev_shot_widget = self._shot_widget_mgr[prev_shot_index]
        self._mediaplayer.seek(prev_shot_widget.get_start_frame_index())


    @log_function_name(color=PRINT_GREEN_COLOR)
    def on_skip_fwd_clicked(self):
        self.pause_video()
        if len(self._shot_widget_mgr) == 0:
            return
        
        # Jump to the beginning of the next shot if it exists.
        frame_index = self._seek_spinbox.value()
        _, end_frame_index = self._db.get_start_end_frame_indexes(frame_index)
        if end_frame_index < self._video_info.frame_count:
            next_shot_index = self._db.get_shot_index(end_frame_index)
            next_shot_widget = self._shot_widget_mgr[next_shot_index]
            self._mediaplayer.seek(next_shot_widget.get_start_frame_index())


    @log_function_name(color=PRINT_GREEN_COLOR)
    def on_edge_detection_toggled(self, checked):
        SBMediaPlayer.detect_edges = checked
        ShotWidget.detect_edges = checked


    @log_function_name(color=PRINT_GREEN_COLOR)
    def on_edge_factor_changed(self, value):
        SBMediaPlayer.edge_factor = value
        ShotWidget.edge_factor = value


    @log_function_name(color=PRINT_GREEN_COLOR)
    def on_split_button_clicked(self):
        self.cmd_split_video()


    @log_function_name(color=PRINT_GREEN_COLOR)
    def on_scan_button_clicked(self):
        self.cmd_scan_selected_shots()


    @log_function_name(color=PRINT_GREEN_COLOR)
    def on_seek_offset_spinbox_changed(self, value):
        self._video_info.seek_offset = value * 0.1


    @log_function_name(color=PRINT_GREEN_COLOR)
    def on_merge_button_clicked(self):
        self.cmd_merge_selected_shots()


    @log_function_name(color=PRINT_GREEN_COLOR)
    def on_shift_backward_button_clicked(self):
        self.cmd_shift_selected_shot_backward()


    @log_function_name(color=PRINT_GREEN_COLOR)
    def on_shift_foreward_button_clicked(self):
        self.cmd_shift_selected_shot_foreward()


    @log_function_name(color=PRINT_GREEN_COLOR)
    def on_debug_button_clicked(self):
        pass


    @log_function_name(color=PRINT_GREEN_COLOR)
    def on_detection_slider_moved(self, value):
        ssim_drop_threshold = self.convert_detection_slider_value_to_ssim_drop_threshold(value)
        self._detection_label.setText(f"{ssim_drop_threshold:.2f}")


    @log_function_name(color=PRINT_GREEN_COLOR)
    def on_speaker_btn_toggled(self, checked):
        if checked:
            self._speaker_button.setIcon(self.style().standardIcon(QStyle.SP_MediaVolumeMuted))
            self.on_volume_slider_moved(0)
        else:
            self._speaker_button.setIcon(self.style().standardIcon(QStyle.SP_MediaVolume))
            self.on_volume_slider_moved(self._volume_slider.value())


    @log_function_name(color=PRINT_GREEN_COLOR)
    def on_volume_slider_click(self, event):
        if event.button() == Qt.LeftButton:
            volume = int(self._volume_slider.minimum() + ((self._volume_slider.maximum() - self._volume_slider.minimum()) * event.x()) / self._volume_slider.width())
            self.on_volume_slider_moved(volume)
            event.accept()
        QSlider.mousePressEvent(self._volume_slider, event)


    @log_function_name(color=PRINT_GREEN_COLOR)
    def on_volume_slider_moved(self, volume):
        if self._speaker_button.isChecked():
            self._volume_label.setText(f"{0:3}%")
            ShotWidget.volume = 0
            self._mediaplayer.set_volume(0)
        else:
            self._volume_label.setText(f"{volume:3}%")
            ShotWidget.volume = volume * 0.01
            self._mediaplayer.set_volume(volume * 0.01)


    @log_function_name(color=PRINT_GREEN_COLOR)
    def on_seek_slider_click(self, event):
        if event.button() == Qt.LeftButton:
            frame_index = round(self._seek_slider.minimum() + ((self._seek_slider.maximum() - self._seek_slider.minimum()) * event.x()) / self._seek_slider.width())
            self.seek_video(frame_index)
            event.accept()
        QSlider.mousePressEvent(self._seek_slider, event)


    @log_function_name(color=PRINT_GREEN_COLOR)
    def on_seek_slider_moved(self, frame_index):
        self.seek_video(frame_index)


    @log_function_name(color=PRINT_GREEN_COLOR)
    def on_seek_spinbox_changed(self, frame_index):
        self.seek_video(frame_index)


    @log_function_name()
    def on_mediaplayer_state_changed(self, state):
        if state == SBMediaPlayer.PlayingState:
            self._play_button.setIcon(self.style().standardIcon(QStyle.SP_MediaPause))
        else:
            self._play_button.setIcon(self.style().standardIcon(QStyle.SP_MediaPlay))


    @log_function_name()
    def on_mediaplayer_frame_changed(self, frame_index):
        self.update_slider_and_spinbox(frame_index)


    @log_function_name(color=PRINT_RED_COLOR)
    def on_mediaplayer_error(self):
        # print(f"{PRINT_RED_COLOR}{self._mediaplayer.errorString()}{PRINT_DEFAULT_COLOR}")
        pass


    @log_function_name(color=PRINT_GREEN_COLOR)
    def on_zoom_changed(self, value):
        self.update_grid_layout()
        self._old_zoom_value = value


    @log_function_name(color=PRINT_GREEN_COLOR)
    def on_scroll(self):
        """Detect which ShotWidgets are visible in the scroll area."""
        viewport = self._scroll_area.viewport()
        scroll_pos = self._scroll_area.verticalScrollBar().value()
        viewport_rect = QRect(0, scroll_pos, viewport.width(), viewport.height())

        visible_shot_widgets = []
        for shot_widget in self._shot_widget_mgr:
            if viewport_rect.intersects(shot_widget.geometry()):
                visible_shot_widgets.append(shot_widget)
        
        ShotWidget.thumbnail_manager.clear_priority_list()
        for shot_widget in visible_shot_widgets:
            shot_widget.request_thumbnail(True)


    @log_function_name(color=PRINT_GREEN_COLOR)
    def on_shot_widget_hovered(self, shot_widget, entering):
        if not self._ui_enabled:
            return
        
        if entering:
            self._hovered_shot_widget = shot_widget
            if self._mediaplayer.is_playing():
                self.pause_video()
        else:
            self._hovered_shot_widget = None


    @log_function_name(color=PRINT_GREEN_COLOR)
    def on_shot_widget_clicked(self, shot_widget, shift_pressed):
        if not self._ui_enabled:
            return
        
        if self._mediaplayer.is_playing():
            self.pause_video()

        # Fetch the shot widget emitting the signal
        shot_index = self._shot_widget_mgr.index(shot_widget)

        if shift_pressed:
            self.cmd_extend_shot_selection(shot_index)
        else:
            self.cmd_select_shot(shot_index)

        start_frame_index = shot_widget.get_start_frame_index()
        self.seek_video(start_frame_index)


    ##
    ## UPDATES
    ##


    @log_function_name()
    def update_window_title(self):
        title = f"{DEFAULT_TITLE} {APP_VERSION}"

        if self._video_info.video_path:
            title += f"   𝗩𝗶𝗱𝗲𝗼 {os.path.basename(self._video_info.video_path)}"
        else:
            title += "   𝗩𝗶𝗱𝗲𝗼 (undefined)"

        if self._db_path:
            title += f"   𝗦𝗵𝗼𝘁 𝗟𝗶𝘀𝘁 {os.path.basename(self._db_path)}"
        else:
            title += "   𝗦𝗵𝗼𝘁 𝗟𝗶𝘀𝘁 (undefined)"

        if self._db.is_dirty():
            title += " ⚠(𝗨𝗻𝘀𝗮𝘃𝗲𝗱 - Right-click to save)"

        self.setWindowTitle(title)


    @log_function_name()
    def update_status_bar(self):
        ratio = self._video_info.frame_width / self._video_info.frame_height if self._video_info.frame_height > 0 else 0
        duration_hms = str(datetime.timedelta(seconds=int(self._video_info.duration)))
        duration_h = self._video_info.duration / 3600 if self._video_info.duration > 0 else 1  # Prevent division by zero
        shots_per_hour = round(len(self._db) / duration_h)

        self._info_label.setText(
            f"𝗗𝘂𝗿𝗮𝘁𝗶𝗼𝗻 {duration_hms}   "
            f"𝗙𝗣𝗦 {self._video_info.fps:.3f}   "
            f"𝗥𝗲𝘀𝗼𝗹𝘂𝘁𝗶𝗼𝗻 {self._video_info.frame_width}x{self._video_info.frame_height} ({ratio:.2f})   "
            f"𝗦𝗵𝗼𝘁𝘀 {len(self._db)} (avg. {shots_per_hour} shots/hour)"
        )


    @log_function_name()
    def on_thumbnail_loaded(self, shot_widget):
        clock = self._clock_emojis[self._loading_step]
        message = f"{clock} Shot {shot_widget.get_shot_number()} updated"
        self._status_bar.showMessage(message, 1000)  # Show message for 1 second
        self._loading_step = (self._loading_step + 1) % len(self._clock_emojis)
    

    def update_ui_state(self):
        enabled = (self._video_info.video_path != None and self._ui_enabled)
        self._seek_slider.setEnabled(enabled)
        self._seek_spinbox.setEnabled(enabled)
        self._play_button.setEnabled(enabled)
        self._stop_button.setEnabled(enabled)
        self._skip_bwd_button.setEnabled(enabled)
        self._skip_fwd_button.setEnabled(enabled)
        self._split_button.setEnabled(enabled)
        self._scan_button.setEnabled(enabled and not self.is_selection_empty())
        self._shift_backward_button.setEnabled(enabled and not self.is_selection_empty())
        self._shift_foreward_button.setEnabled(enabled and not self.is_selection_empty())
        self._merge_button.setEnabled(enabled and not self.is_selection_empty())
        self._zoom_spinbox.setEnabled(enabled)
        #self._detection_slider.setEnabled(enabled and not self.is_selection_empty())


    def update_slider_and_spinbox(self, frame_index):
        self._seek_slider.blockSignals(True)
        self._seek_spinbox.blockSignals(True)

        if self._seek_slider.value() != frame_index:
            self._seek_slider.setValue(frame_index)

            elapsed_time_s = frame_index / self._video_info.fps
            if SLIDER_TIMESTAMP_MILLISECONDS:
                hours = int(elapsed_time_s // 3600)
                minutes = int((elapsed_time_s % 3600) // 60)
                seconds = int(elapsed_time_s % 60)
                milliseconds = int((elapsed_time_s - int(elapsed_time_s)) * 1000)
                timestamp = f"{hours:01}:{minutes:02}:{seconds:02}:{milliseconds:03}"
            else:
                timestamp = str(datetime.timedelta(seconds=int(elapsed_time_s)))
            self._timestamp_label.setText(timestamp)

        if self._seek_spinbox.value() != frame_index:
            self._seek_spinbox.setValue(frame_index)

        self._seek_slider.blockSignals(False)
        self._seek_spinbox.blockSignals(False)


    def reset_all(self):
        self.stop_video()
        self._mediaplayer.reset_frame()
        self._history.clear()
        self.clear_shot_widgets()
        self._db.clear_shots()
        self._db_path = None
        self._video_info.clear_info()
        ShotWidget.thumbnail_manager.clear()
        ShotWidget.thumbnail_manager.set_video_info(self._video_info)
        self.update_ui_state()
        self.update_status_bar()
        self.update_window_title()
        self.statusBar().showMessage("Load a video.")


    def clear_shot_widgets(self):
        self.deselect_all()
        for widget in self._shot_widget_mgr:
            self._grid_layout.removeWidget(widget)
            widget.hide()
            widget.deleteLater()
        self._shot_widget_mgr.clear()


    @log_function_name()
    def update_grid_layout(self):
        # Disable updates to prevent tons of repaints
        # self._scroll_area.setUpdatesEnabled(False)

        # Width of the ScrollArea strictly dedicated to shot widgets (accounts for margins, scrollbar width and borders)
        scroll_area_available_width = self._scroll_area.width() - (2 * SCROLL_AREA_BORDER) - (2 * GRID_LAYOUT_MARGIN) - SCROLL_AREA_VERT_SCROLLBAR_WIDTH

        num_img_per_row = self._zoom_spinbox.value()
        new_shot_widget_size = ShotWidget.evaluate_widget_size(num_img_per_row)
        needs_resize_shot_widgets = (self._shot_widget_mgr.get_shot_widget_size() != new_shot_widget_size) if len(self._shot_widget_mgr) > 0 else False

        # Store the index of the first shot widget entirely visible in the ScrollArea
        if needs_resize_shot_widgets:
            old_num_cols = self._old_zoom_value
            old_shot_widget_size = ShotWidget.evaluate_widget_size(self._old_zoom_value)
            old_scroll_pos = self._scroll_area.verticalScrollBar().value()
            old_topleft_shot_widget_index = old_num_cols * math.ceil(max(0, (old_scroll_pos - GRID_LAYOUT_MARGIN + GRID_LAYOUT_SPACING)) / (old_shot_widget_size[1] + GRID_LAYOUT_SPACING))
            # print(f"old_num_cols = {old_num_cols}   old_shot_widget_size = {old_shot_widget_size}   old_scroll_pos = {old_scroll_pos}   old_topleft_shot_widget_index = {old_topleft_shot_widget_index} -> displayed = {old_topleft_shot_widget_index + 1}")

        progress_dialog = None
        if len(self._db) > 0:
            if len(self._db) > 10:
                # Create a progress dialog
                progress_dialog = QProgressDialog("Updating shots...", "Cancel", 0, len(self._db), self)
                progress_dialog.setWindowModality(Qt.WindowModal)
                progress_dialog.setWindowTitle("Shot creation")
                progress_dialog.setWindowFlags(progress_dialog.windowFlags() & ~Qt.WindowContextHelpButtonHint)
                progress_dialog.setMinimumDuration(0)
                progress_dialog.setValue(0)

            i, j = 0, 0
            while i < len(self._shot_widget_mgr) and j < len(self._db):
                shot_widget = self._shot_widget_mgr[i]
                start_frame_index = self._db[j]

                shot_widget.set_shot_number(i + 1)  # /!\ 1-based (not 0-based)
                if needs_resize_shot_widgets:
                    shot_widget.resize(num_img_per_row)
                else:
                    shot_widget.update_progress_bar_format()

                widget_start_frame_index = shot_widget.get_start_frame_index()
                if widget_start_frame_index == start_frame_index:
                    # This widget is OK, move on to the next one
                    i += 1
                    j += 1
                elif widget_start_frame_index < start_frame_index:
                    # This widget is no longer needed, remove it
                    del self._shot_widget_mgr[i]
                else:
                    # Start frame index in _db is missing from _shot_widgets, insert it
                    self._shot_widget_mgr.create_shot_widget(*self._db.get_start_end_frame_indexes(start_frame_index), num_img_per_row)
                    i += 1
                    j += 1

                if progress_dialog:
                    progress_dialog.setValue(j)
                    if progress_dialog.wasCanceled():
                        progress_dialog.close()
                        progress_dialog = None
                        break

            # If any widget remain in _shot_widgets, remove them
            if not progress_dialog or not progress_dialog.wasCanceled():
                while i < len(self._shot_widget_mgr):
                    del self._shot_widget_mgr[i]

                # If any shot remain in _db, add them at the end
                while j < len(self._db):
                    self._shot_widget_mgr.create_shot_widget(*self._db.get_start_end_frame_indexes(self._db[j]), num_img_per_row)
                    j += 1
                    if progress_dialog:
                        progress_dialog.setValue(j)
                        if progress_dialog.wasCanceled():
                            progress_dialog.close()
                            progress_dialog = None
                            break

            if progress_dialog:
                progress_dialog.close()
                progress_dialog = None

        # Update grid layout
        new_shot_widget_size = self._shot_widget_mgr.get_shot_widget_size()
        new_num_cols = (scroll_area_available_width + GRID_LAYOUT_SPACING) // (new_shot_widget_size[0] + GRID_LAYOUT_SPACING)

        # Add widgets to the grid layout in order
        for i, shot_widget in enumerate(self._shot_widget_mgr):
            row = i // new_num_cols
            col = i % new_num_cols
            self._grid_layout.addWidget(shot_widget, row, col, 1, 1)

        # Remove widgets from the grid layout that are no longer in _shot_widgets
        widgets_to_remove = [
            self._grid_layout.itemAt(i).widget()
            for i in range(self._grid_layout.count())
            if self._grid_layout.itemAt(i).widget() not in self._shot_widget_mgr
        ]
        for widget in widgets_to_remove:
            self._grid_layout.removeWidget(widget)
            widget.hide()
            widget.deleteLater()

        # Scroll so that the first shot widget previously entirely visible in the ScrollArea is still part of the first row
        if needs_resize_shot_widgets:
            new_top_row = old_topleft_shot_widget_index // new_num_cols
            new_scroll_pos = GRID_LAYOUT_MARGIN + new_top_row * (new_shot_widget_size[1] + GRID_LAYOUT_SPACING) - GRID_LAYOUT_SPACING
            self._scroll_area.verticalScrollBar().setValue(new_scroll_pos)
            # new_topleft_shot_widget_index = new_num_cols * new_top_row
            # print(f"new_num_cols = {new_num_cols}   new_shot_widget_size = {new_shot_widget_size}   new_scroll_pos = {new_scroll_pos}   new_topleft_shot_widget_index = {new_topleft_shot_widget_index} -> displayed {new_topleft_shot_widget_index + 1}   new_top_row = {new_top_row}")

        # Re-enable updates and trigger a single repaint
        # self._scroll_area.setUpdatesEnabled(True)
        # self._scroll_area.update()
        self.update_status_bar()
        self.update_window_title()


    ##
    ## ACTIONS
    ##


    @log_function_name(color=PRINT_YELLOW_COLOR)
    def set_video(self, url):
        assert url
        if not url:
            return

        self.reset_all()        
        self.enable_ui(False)

        video_path = url.toLocalFile()
        cap = cv2.VideoCapture(video_path)
        fps = cap.get(cv2.CAP_PROP_FPS)
        frame_width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
        frame_height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
        frame_count = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
        cap.release()

        self._video_info.set_info(video_path, fps, frame_width, frame_height, frame_count)
        self._video_info.seek_offset = self._seek_offset_spinbox.value() * 0.1
        
        self._mediaplayer.set_video_info(self._video_info)
        self._shot_widget_mgr.set_video_info(self._video_info)

        self.on_volume_slider_moved(self._volume_slider.value())

        self._seek_slider.setRange(0, frame_count - 1)
        self._seek_spinbox.setRange(0, frame_count - 1)

        self._db.set_frame_count(frame_count)
        self._db.add_shot(0)  # Add a single shot covering the whole video

        self.update_window_title()
        self.update_grid_layout()
        self.select_shot_widgets(0, 0)
        self.update_ui_state()
        self.enable_ui(True)


    @log_function_name(color=PRINT_YELLOW_COLOR)
    def open_shot_list(self, json_path):
        if json_path is None or not os.path.exists(json_path):
            return

        self.enable_ui(False)
        self._history.clear()
        self._db.load_from_json(json_path)
        self._db_path = json_path
        #self.add_filename_to_recent(json_path)  # update 'Open Recent' menu.
        if self._video_info.frame_count != self._db.get_frame_count():
            QMessageBox.warning(self, "Warning", "The shot list does not match the video.")
            self._db.clear_shots()
            self.deselect_all()

        ShotWidget.thumbnail_manager.clear()
        ShotWidget.thumbnail_manager.add_frame_indexes_to_queue(self._db.get_shots())

        self.update_status_bar()  # Display info right away as update_grid_layout() might take a long time to execute
        self.update_window_title()
        self.update_grid_layout()
        self.enable_ui(True)


    @log_function_name(color=PRINT_YELLOW_COLOR)
    def seek_video(self, frame_index):
        self._mediaplayer.seek(frame_index)


    @log_function_name(color=PRINT_YELLOW_COLOR)
    def play_video(self):
        self._mediaplayer.play()


    @log_function_name(color=PRINT_YELLOW_COLOR)
    def pause_video(self):
        self._mediaplayer.pause()


    @log_function_name(color=PRINT_YELLOW_COLOR)
    def resume_video(self):
        self._mediaplayer.resume()


    @log_function_name(color=PRINT_YELLOW_COLOR)
    def stop_video(self):
        self._mediaplayer.stop()


    @log_function_name(color=PRINT_GREEN_COLOR)
    def split_video(self, frame_index):
        if len(self._shot_widget_mgr) == 0:
            return

        self.pause_video()

        # Get the current frame and evaluate the start and end frames of the shot
        start_frame_index, _ = self._db.get_start_end_frame_indexes(frame_index)
        if frame_index == start_frame_index:
            return
        
        self.enable_ui(False)
        self.deselect_all()

        # Insert a new frame in the database
        new_shot_index = self._db.add_shot(frame_index)

        # Initialize the thumbnail of the original shot widget
        original_shot_widget = self._shot_widget_mgr[new_shot_index - 1]
        original_shot_widget.initialise_thumbnail()

        # Update the grid layout
        self.update_grid_layout()
        self.select_shot_widgets(new_shot_index, new_shot_index)
        self.enable_ui(True)
        return True

    
    @log_function_name()
    def detect_shots_ssim(self, start_frame_index, end_frame_index):
        assert self._video_info.video_path
        if not self._video_info.video_path:
            return

        self.stop_video()
        self.seek_video(start_frame_index)
        self.enable_ui(False)

        # Define target width and compute target height while maintaining aspect ratio
        TARGET_WIDTH = self._downscale_spinbox.value()
        TARGET_HEIGHT = round(self._video_info.frame_height * (TARGET_WIDTH / self._video_info.frame_width))
        FRAME_SIZE = TARGET_WIDTH * TARGET_HEIGHT

        # Convert frame index to timestamp (in seconds) for FFmpeg seeking
        START_POS = max(0, (start_frame_index + self._video_info.seek_offset) / self._video_info.fps)  # frame position in seconds

        # FFmpeg command to extract frames as grayscale
        ffmpeg_cmd = [
            "ffmpeg",
            "-loglevel", "quiet",
            "-ss", str(START_POS),  # Fast seek FIRST
            "-i", self._video_info.video_path,  # Input file AFTER
            "-vframes", str(end_frame_index - start_frame_index),
            "-vf", f"scale={TARGET_WIDTH}:{TARGET_HEIGHT}, format=gray",  # Scale and convert to grayscale
            "-f", "rawvideo",
            "-pix_fmt", "gray",
            "-nostdin",
            "-"
        ]

        # Run FFmpeg without showing a console window
        process = subprocess.Popen(
            ffmpeg_cmd,
            stdout=subprocess.PIPE,  # Capture stdout
            stderr=subprocess.DEVNULL,  # Discard stderr
            **FFMPEG_NOWINDOW_KWARGS
        )

        # Create a progress dialog
        progress_dialog = QProgressDialog("Detecting shots... (time remaining: --:--:--)", "Cancel", start_frame_index, end_frame_index, self)
        progress_dialog.setWindowModality(Qt.WindowModal)
        progress_dialog.setWindowTitle("Shot detection")
        progress_dialog.setWindowFlags(progress_dialog.windowFlags() & ~Qt.WindowContextHelpButtonHint)
        progress_dialog.setMinimumDuration(0)
        progress_dialog.setValue(start_frame_index)

        frame_index = start_frame_index
        prev_frame = None
        prev_ssim = None  # Stores SSIM of the previous frame
        prev_prev_ssim = None  # Stores SSIM of the frame before the previous one

        ssim_drop_threshold = self.convert_detection_slider_value_to_ssim_drop_threshold(self._detection_slider.value())

        plot_enabled = self._plot_checkbox.isChecked()
        if plot_enabled:
            # Initialize Matplotlib figure for live plotting
            plt.ion()  # Turn on interactive mode
            fig, ax = plt.subplots()
            ax.set_xlabel("Frame Index")
            ax.set_ylabel("Frame similarity (SSIM)")
            ax.set_ylim(0, 1)  # SSIM values range from 0 to 1
            ax.set_title("Live SSIM Plot")

            # Create empty arrays to store SSIM values
            x_data = np.array([], dtype=int)  # Frame indexes
            y_data = np.array([], dtype=float)  # SSIM values
            (line,) = ax.plot([], [], "r-")  # Red line for SSIM values

        start_timer = QElapsedTimer()
        start_timer.start()  # Starts tracking elapsed time

        while frame_index < end_frame_index:
            # Read the next frame
            raw_frame = process.stdout.read(FRAME_SIZE)
            if not raw_frame:
                break

            # Convert raw frame to numpy array
            frame = np.frombuffer(raw_frame, dtype=np.uint8).reshape((TARGET_HEIGHT, TARGET_WIDTH))

            # Calculate SSIM if previous frame exists
            if prev_frame is not None:
                current_ssim, _ = ssim(prev_frame, frame, full=True)

                if plot_enabled and plt.fignum_exists(fig.number):
                    # Append new values to NumPy arrays
                    x_data = np.append(x_data, frame_index)
                    y_data = np.append(y_data, current_ssim)

                    # Update plot data
                    line.set_xdata(x_data)
                    line.set_ydata(y_data)

                    # Adjust plot limits dynamically
                    ax.set_xlim(max(0, frame_index - 100), frame_index + 1)  # Keep 100 frames visible
                    ax.relim()
                    ax.autoscale_view(True, True, True)  # Adjust Y-axis dynamically

                    plt.draw()
                else:
                    plot_enabled = False

                # Ensure we have 3 SSIM values before making a decision
                if prev_ssim is not None and prev_prev_ssim is not None:
                    # Detect a sudden drop '\' in similarity
                    simple_condition = prev_prev_ssim - prev_ssim >= ssim_drop_threshold
                    # Detect a 'V' spike (sudden drop followed by a rise) in similarity
                    double_condition = ((prev_prev_ssim - prev_ssim >= SIM_DROP_THRESHOLD_MAX and current_ssim - prev_ssim >= ssim_drop_threshold) or
                                        (prev_prev_ssim - prev_ssim >= ssim_drop_threshold and current_ssim - prev_ssim >= SIM_DROP_THRESHOLD_MAX))
                    condition = double_condition if self._double_condition_checkbox.isChecked() else simple_condition
                    if condition:
                        # Add shot at the previous frame
                        cut_frame_index = frame_index - 1
                        shot_index = self._db.add_shot(cut_frame_index)
                        ShotWidget.thumbnail_manager.add_frame_index_to_queue(cut_frame_index)
                        self.seek_video(cut_frame_index)

                # Shift SSIM values
                prev_prev_ssim = prev_ssim
                prev_ssim = current_ssim

            # Update previous frame and increment frame index
            prev_frame = frame
            frame_index += 1

            # Update the progress dialog
            progress_dialog.setValue(frame_index)
            if frame_index % 25 == 0:
                elapsed_time = start_timer.elapsed()  # Elapsed time in milliseconds
                estimated_total_time = elapsed_time * ((end_frame_index - start_frame_index) / (frame_index - start_frame_index))
                remaining_time = int(estimated_total_time - elapsed_time)
                remaining_time_hms = QTime(0, 0).addMSecs(remaining_time).toString("hh:mm:ss")
                progress_dialog.setLabelText(f"Detecting shots... (time remaining: {remaining_time_hms})")
            if progress_dialog.wasCanceled():
                break

        # Close FFmpeg process
        process.stdout.close()
        process.wait()
        progress_dialog.close()

        # Display the total time taken to detect shots
        # total_time_hms = QTime(0, 0).addMSecs(start_timer.elapsed()).toString("hh:mm:ss")
        # print(f"Shots detected in {total_time_hms}")
        start_timer.invalidate()

        # Close the graph if it's still open
        if plot_enabled and plt.fignum_exists(fig.number):
            plt.ioff()  # Turn off interactive mode when done
            plt.show()  # Show final graph

        # Update the grid layout
        self.stop_video()
        self.update_status_bar()  # Display info right away as update_grid_layout() may take a long time to execute
        self.update_grid_layout()
        self.enable_ui(True)
        return True


    @log_function_name(color=PRINT_GREEN_COLOR)
    def merge_selected_shots(self):
        if len(self._shot_widget_mgr) == 0:
            return

        self.pause_video()

        if self._selection_first_index == self._selection_last_index:
            return

        self.enable_ui(False)

        shot_index_min, shot_index_max = self.get_selection_index_min_max()
        shot_widget_min, shot_widget_max = self._shot_widget_mgr[shot_index_min], self._shot_widget_mgr[shot_index_max]
        start_frame_index, end_frame_index = shot_widget_min.get_start_frame_index(), shot_widget_max.get_end_frame_index()

        shot_widget_min.set_end_frame_index(end_frame_index, True)

        self.deselect_all()
        del self._db[shot_index_min + 1 : shot_index_max + 1]
        
        # Update the grid layout
        self.update_grid_layout()

        # Reselect the first shot widget
        self.select_shot_widgets(shot_index_min, shot_index_min)
        self.seek_video(start_frame_index)
        self.enable_ui(True)

        return shot_widget_min


    # DEBUG
    @log_function_name(color=PRINT_GREEN_COLOR)
    def shift_selected_shot_backward(self):
        if len(self._shot_widget_mgr) == 0:
            return

        self.pause_video()

        if self._selection_first_index != self._selection_last_index or self._selection_first_index == 0:
            return

        curr_shot_index = self._selection_first_index
        prev_shot_index = self._selection_first_index - 1

        curr_shot_widget = self._shot_widget_mgr[curr_shot_index]
        prev_shot_widget = self._shot_widget_mgr[prev_shot_index]

        curr_start_frame_index = curr_shot_widget.get_start_frame_index()
        new_start_frame_index = curr_start_frame_index - 1
        if prev_shot_widget.get_start_frame_index() >= new_start_frame_index:
            return

        self.enable_ui(False)

        self._db[curr_shot_index] = new_start_frame_index
        self._shot_widget_mgr.offset_start_frame_index(curr_start_frame_index, -1)
        prev_shot_widget.initialise_thumbnail()

        # Update the grid layout
        self.update_grid_layout()

        self.seek_video(new_start_frame_index)
        self.enable_ui(True)

        return True


    # DEBUG
    @log_function_name(color=PRINT_GREEN_COLOR)
    def shift_selected_shot_foreward(self):
        if len(self._shot_widget_mgr) == 0:
            return

        self.pause_video()

        if self._selection_first_index != self._selection_last_index:
            return

        curr_shot_index = self._selection_first_index
        curr_shot_widget = self._shot_widget_mgr[curr_shot_index]

        curr_start_frame_index = curr_shot_widget.get_start_frame_index()
        new_start_frame_index = curr_start_frame_index + 1
        if new_start_frame_index >= curr_shot_widget.get_end_frame_index() - 1:
            return

        self.enable_ui(False)

        self._db[curr_shot_index] = new_start_frame_index
        self._shot_widget_mgr.offset_start_frame_index(curr_start_frame_index, 1)

        # Update the grid layout
        self.update_grid_layout()

        self.seek_video(new_start_frame_index)
        self.enable_ui(True)

        return True


    @log_function_name(color=PRINT_GREEN_COLOR)
    def save_shot_list(self, path):
        if path is None:
            return
        
        self.enable_ui(False)
        self._db.save_to_json(path)
        self._db_path = path
        # self.push_filename_to_recent(path)  # update 'Open Recent' menu.
        message = f"File saved successfully: {self._db_path}"
        self._status_bar.showMessage(message, 5000)  # Show message for 5 seconds
        self.update_window_title()
        self.enable_ui(True)


    @log_function_name(color=PRINT_GREEN_COLOR)
    def export_selection(self, ask_for_path):
        if not self._video_info.video_path:
            QMessageBox.warning(self, "Export Error", "Please load a video first.")
            return

        if self._selection_first_index is None or self._selection_last_index is None:
            QMessageBox.warning(self, "Export Error", "Please select shots first.")
            return

        self.pause_video()

        # Calculate start time in seconds
        shot_index_min, shot_index_max = self.get_selection_index_min_max()
        shot_widget_min, shot_widget_max = self._shot_widget_mgr[shot_index_min], self._shot_widget_mgr[shot_index_max]
        start_frame_index, end_frame_index = shot_widget_min.get_start_frame_index(), shot_widget_max.get_end_frame_index()

        START_POS = max(0, (start_frame_index + self._video_info.seek_offset) / self._video_info.fps)  # frame position in seconds

        if ask_for_path:
            save_path, _ = QFileDialog.getSaveFileName(
                self, "Export Video", "", "MP4 Files (*.mp4);;All Files (*)"
            )
            if not save_path:
                return
        else:
            save_path = self.make_export_path(START_POS, extension=".mp4")

        if os.path.exists(save_path) and not QMessageBox.question(self, 'File Exists', f"The file {save_path} already exists. Do you want to overwrite it?", QMessageBox.Yes | QMessageBox.No, QMessageBox.No) == QMessageBox.Yes:
           return

        self.enable_ui(False)

        # Show a modal dialog
        dialog = QDialog(self)
        dialog.setWindowTitle("Exporting...")
        dialog.setModal(True)
        dialog.setWindowFlags(dialog.windowFlags() & ~Qt.WindowContextHelpButtonHint)
        layout = QVBoxLayout(dialog)
        layout.addWidget(QLabel("Please wait while the shot selection is exported..."))
        dialog.setLayout(layout)
        dialog.show()
        QApplication.processEvents()  # Keep UI responsive

        # Build the ffmpeg command
        ffmpeg_cmd = [
            "ffmpeg",
            "-loglevel", "quiet",
            "-y",  # Overwrite without asking
            "-ss", str(START_POS),  # Apply seeking **after** input for accuracy
            "-i", self._video_info.video_path,  # Input file first
            # "-c", "copy",  # Copy streams instead of re-encoding
            "-c:v", "libx264",  # Re-encode video to ensure precision
            # "-preset", "ultrafast",  # Minimize encoding overhead
            "-preset", "veryfast",  # Good balance of speed and quality
            "-crf", "18",  # High quality (lower CRF = better quality)
            "-tune", "film",  # Optimize for natural videos
            "-vframes", str(end_frame_index - start_frame_index),  # Extract exact frame count
            save_path  # Output file
        ]

        # Run the FFmpeg command
        try:
            subprocess.run(ffmpeg_cmd, check=True, **FFMPEG_NOWINDOW_KWARGS)
            message = f"Export successful: {save_path}"
            self._status_bar.showMessage(message, 5000)  # Show message for 5 seconds
        except subprocess.CalledProcessError as e:
            error_message = f"Export failed: {e}"
            self._status_bar.showMessage(error_message, 5000)
            QMessageBox.warning(self, "Export Error", error_message)
            print(error_message)

        # Close the dialog automatically when finished
        dialog.close()
        self.enable_ui(True)


    @log_function_name(color=PRINT_GREEN_COLOR)
    def export_single_frame(self, frame_index, ask_for_path=True):
        if not self._video_info.video_path:
            QMessageBox.warning(self, "Export Error", "Please load a video first.")
            return

        self.pause_video()

        # Calculate timestamp in seconds
        START_POS = max(0, (frame_index + self._video_info.seek_offset) / self._video_info.fps)  # frame position in seconds

        if ask_for_path:
            save_path, _ = QFileDialog.getSaveFileName(
                self, "Export Frame", "", "JPEG Files (*.jpg);;PNG Files (*.png);;All Files (*)"
            )
            if not save_path:
                return
        else:
            save_path = self.make_export_path(START_POS, extension=".jpg")

        if os.path.exists(save_path) and not QMessageBox.question(
            self, "File Exists", f"The file {save_path} already exists. Do you want to overwrite it?",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No
        ) == QMessageBox.Yes:
            return

        self.enable_ui(False)

        # Show a modal dialog
        dialog = QDialog(self)
        dialog.setWindowTitle("Exporting Frame...")
        dialog.setModal(True)
        dialog.setWindowFlags(dialog.windowFlags() & ~Qt.WindowContextHelpButtonHint)
        layout = QVBoxLayout(dialog)
        layout.addWidget(QLabel("Please wait while the frame is exported..."))
        dialog.setLayout(layout)
        dialog.show()
        QApplication.processEvents()  # Keep UI responsive

        # FFmpeg command to extract a single frame
        ffmpeg_cmd = [
            "ffmpeg",
            "-loglevel", "quiet",
            "-y",  # Overwrite without asking
            "-ss", str(START_POS),  # Seek to the frame
            "-i", self._video_info.video_path,  # Input file
            "-frames:v", "1",  # Export only one frame
            "-q:v", "2",  # High quality (lower = better quality, range: 2-31)
            "-update", "1",  # Ensure single image update (for PNG/JPEG output)
            save_path  # Output file
        ]

        # Run FFmpeg command
        try:
            subprocess.run(ffmpeg_cmd, check=True, **FFMPEG_NOWINDOW_KWARGS)
            message = f"Frame exported successfully: {save_path}"
            self._status_bar.showMessage(message, 5000)  # Show message for 5 seconds
        except subprocess.CalledProcessError as e:
            error_message = f"Export failed: {e}"
            self._status_bar.showMessage(error_message, 5000)
            QMessageBox.warning(self, "Export Error", error_message)
            print(error_message)

        # Close the dialog and re-enable UI
        dialog.close()
        self.enable_ui(True)


    ##
    ## UNDO/REDO
    ##


    def restore_selection(self, data):
        old_first_index = data['first_index']
        old_last_index = data['last_index']
        self.select_shot_widgets(old_first_index, old_last_index)


    def restore_context(self, data):
        self.deselect_all()
        frame_indexes = data['frame_indexes']
        self._db.set_shots(frame_indexes)
        self.update_grid_layout()
        self.restore_selection(data)


    @command_selection_context
    def cmd_select_all(self):
        return self.select_all()


    @command_selection_context
    def cmd_deselect_all(self):
        return self.deselect_all()


    @command_selection_context
    def cmd_select_shot(self, shot_index):
        return self.select_shot_widgets(shot_index, shot_index)


    @command_selection_context
    def cmd_extend_shot_selection(self, shot_index):
        return self.extend_shot_selection(shot_index)


    @command_full_context
    def cmd_split_video(self):
        return self.split_video(self._seek_spinbox.value())


    @command_full_context
    def cmd_scan_selected_shots(self):
        if self.is_selection_empty():
            return
        
        if self._selection_first_index == self._selection_last_index:
            shot_widget = self._shot_widget_mgr[self._selection_first_index]
            shot_widget.initialise_thumbnail()
        else:
            shot_widget = self.merge_selected_shots()

        if shot_widget is None:
            return
        self.detect_shots_ssim(shot_widget.get_start_frame_index(), shot_widget.get_end_frame_index())
        return shot_widget


    @command_full_context
    def cmd_merge_selected_shots(self):
        return self.merge_selected_shots()


    @command_full_context
    def cmd_shift_selected_shot_backward(self):
        return self.shift_selected_shot_backward()


    @command_full_context
    def cmd_shift_selected_shot_foreward(self):
        return self.shift_selected_shot_foreward()


    ##
    ## SELECTION
    ##


    # @log_function_name(color=PRINT_GRAY_COLOR)
    def is_selection_empty(self):
        return self._selection_first_index == None


    # @log_function_name(color=PRINT_GRAY_COLOR)
    def select_all(self):
        if self._selection_first_index == 0 and self._selection_last_index == len(self._shot_widget_mgr) - 1:
            return
        
        self._selection_first_index = 0
        self._selection_last_index = len(self._shot_widget_mgr) - 1
        for _, shot_widget in enumerate(self._shot_widget_mgr):
            shot_widget.set_selected(True)
        self.update_ui_state()
        return True


    # @log_function_name(color=PRINT_GRAY_COLOR)
    def deselect_all(self):
        if self._selection_first_index == None:
            return

        shot_index_min, shot_index_max = self.get_selection_index_min_max()
        for i in range(shot_index_min, shot_index_max + 1):
            self._shot_widget_mgr[i].set_selected(False)
        self._selection_first_index = None
        self._selection_last_index = None
        self.update_ui_state()
        return True


    # @log_function_name(color=PRINT_GRAY_COLOR)
    def select_shot_widgets(self, first_index, last_index):
        if len(self._shot_widget_mgr) == 0:
            return

        # If no valid selection range is given, clear selection
        if first_index is None:
            assert last_index is None
            self.deselect_all()
            return

        # Ensure old selection indexes are valid
        if self._selection_first_index is None or self._selection_last_index is None:
            old_index_min, old_index_max = None, None
        else:
            old_index_min = min(self._selection_first_index, self._selection_last_index)
            old_index_max = max(self._selection_first_index, self._selection_last_index)

        new_index_min, new_index_max = min(first_index, last_index), max(first_index, last_index)

        if new_index_min == old_index_min and new_index_max == old_index_max:
            return  # No changes in selection

        if old_index_min is None:
            # If there was no previous selection, simply select the new range
            select_indices = set(range(new_index_min, new_index_max + 1))
        else:
            # Deselect the widgets present in old range but not in new range)
            deselect_indices = set(range(old_index_min, old_index_max + 1)) - set(range(new_index_min, new_index_max + 1))
            for i in deselect_indices:
                self._shot_widget_mgr[i].set_selected(False)  # Assume ShotWidget has set_selected(bool)

            # Find the widgets to select (present in new range but not in old range)
            select_indices = set(range(new_index_min, new_index_max + 1)) - set(range(old_index_min, old_index_max + 1))

        # Select new widgets
        for i in select_indices:
            self._shot_widget_mgr[i].set_selected(True)

        # Update selection indexes
        self._selection_first_index = first_index
        self._selection_last_index = last_index
        self.update_ui_state()
        return True


    # @log_function_name(color=PRINT_GRAY_COLOR)
    def extend_shot_selection(self, shot_index):
        # If the new index doesn't change the extented selection, do nothing
        if shot_index == self._selection_last_index:
            return

        # If the selection is empty, select from 0 to the new index
        if self._selection_first_index == None:
            for i in range(0, shot_index + 1):
                self._shot_widget_mgr[i].set_selected(True)
            self._selection_first_index = 0
            self._selection_last_index = shot_index
            self.update_ui_state()
            return

        # Else either extend or reduce the selection
        if self._selection_first_index <= self._selection_last_index:
            if shot_index < self._selection_first_index:  # extend the selection to the left
                for i in range(shot_index, self._selection_first_index):
                    self._shot_widget_mgr[i].set_selected(True)
                for i in range(self._selection_first_index + 1, self._selection_last_index + 1):
                    self._shot_widget_mgr[i].set_selected(False)
            elif shot_index < self._selection_last_index:  # reduce the selection
                for i in range(shot_index + 1, self._selection_last_index + 1):
                    self._shot_widget_mgr[i].set_selected(False)
            else:  # extend the selection to the right
                for i in range(self._selection_last_index + 1, shot_index + 1):
                    self._shot_widget_mgr[i].set_selected(True)
        else:  # inverted min and max
            if shot_index > self._selection_first_index:  # extend the selection to the right
                for i in range(self._selection_last_index, self._selection_first_index):
                    self._shot_widget_mgr[i].set_selected(False)
                for i in range(self._selection_first_index + 1, shot_index + 1):
                    self._shot_widget_mgr[i].set_selected(True)
            elif shot_index > self._selection_last_index:  # reduce the selection
                for i in range(self._selection_last_index, shot_index):
                    self._shot_widget_mgr[i].set_selected(False)
            else:  # extend the selection to the left
                for i in range(shot_index, self._selection_last_index):
                    self._shot_widget_mgr[i].set_selected(True)

        self._selection_last_index = shot_index
        self.update_ui_state()
        return True


    def get_selection_index_min_max(self):
        if self._selection_first_index is None or self._selection_last_index is None:
            return None, None
        
        return min(self._selection_first_index, self._selection_last_index), max(self._selection_first_index, self._selection_last_index)


    ##
    ## HELPERS
    ##


    def enable_ui(self, enable):
        self._ui_enabled = enable
        self.update_ui_state()


    def frame_index_to_ms(self, frame_index):
        return int(frame_index * 1000 / self._video_info.fps)  # in milliseconds


    def ms_to_frame_index(self, time_ms):
        return int(time_ms * 0.001 * self._video_info.fps)


    def convert_detection_slider_value_to_ssim_drop_threshold(self, value):
        return (SIM_DROP_THRESHOLD_MAX - SIM_DROP_THRESHOLD_MIN) * (value / DETECTION_SLIDER_STEPS) + SIM_DROP_THRESHOLD_MIN


    def make_export_path(self, START_POS, extension):
        # Extract filename and remove extension
        filename, _ = os.path.splitext(os.path.basename(self._video_info.video_path))  # Split the extension
        title = filename.replace(" ", "")  # Remove spaces
        timestamp = f"{int(START_POS // 3600):02}{int((START_POS % 3600) // 60):02}{int(START_POS % 60):02}"

        # Split title and year, assuming the format "Title (Year)"
        if "(" in title and title.endswith(")"):
            title, year = title.rsplit("(", 1)
            year = year[:-1]  # Remove closing ')'
            filename = f"{title}_{year}_{timestamp}{extension}"
        else:
            filename = f"{title}_{timestamp}{extension}"

        # Create "Export" subdirectory
        export_dir = os.path.join(os.path.dirname(self._video_info.video_path), "Export")
        os.makedirs(export_dir, exist_ok=True)  # Ensure it exists

        # Full export path
        save_path = os.path.join(export_dir, filename)
        return save_path


    def ask_to_save_if_dirty(self):
        # Save the database if dirty
        if self._db and self._db.is_dirty():
            if QMessageBox.question(
                self, "Save Shot list?", f"The shot list have changed. Do you want to save it?",
                QMessageBox.Yes | QMessageBox.No, QMessageBox.Yes
            ) == QMessageBox.Yes:
                self.on_menu_save()


    def closeEvent(self, event):
        if self._mediaplayer:
            self._mediaplayer.stop()

        self.ask_to_save_if_dirty()

        event.accept()


##
## MAIN
##


if __name__ == '__main__':
    app = QApplication(sys.argv)

    # Set the application style
    app.setStyle('Fusion')

    # Set a dark style for the application
    # palette = QPalette()
    # palette.setColor(QPalette.Window, QColor(53, 53, 53))
    # palette.setColor(QPalette.WindowText, Qt.white)
    # palette.setColor(QPalette.Button, QColor(0, 0, 0))
    # palette.setColor(QPalette.ButtonText, Qt.white)
    # app.setPalette(palette)

    # Create the main window
    window = ShotBoard()
    window.show()

    sys.exit(app.exec_())
