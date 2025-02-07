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

import ffmpeg
import subprocess
import cv2
import numpy as np
from skimage.metrics import structural_similarity as ssim
import matplotlib.pyplot as plt
import os
import sys
import datetime
from PyQt5.QtCore import Qt, pyqtSignal, QRect, QTimer, QTime, QElapsedTimer
from PyQt5.QtWidgets import QApplication, QMainWindow, QWidget, QShortcut, QMessageBox, QFileDialog, QProgressDialog
from PyQt5.QtWidgets import QSplitter, QHBoxLayout, QVBoxLayout, QGridLayout, QScrollArea, QSlider, QSpinBox
from PyQt5.QtWidgets import QLabel, QPushButton, QToolButton, QCheckBox
from PyQt5.QtWidgets import QAction, QStyle
from PyQt5.QtGui import QKeySequence, QIcon, QPalette, QColor
from PyQt5.QtMultimedia import QMediaPlayer, QMediaContent
from PyQt5.QtMultimediaWidgets import QVideoWidget

APP_VERSION = "0.4.2"

# Main UI
DEFAULT_TITLE = "ShotBoard"
SPLITTER_HANDLE_WIDTH = 3
UPDATE_TIMER_INTERVAL = 1000  # 1/2 s

# Detection
MIN_SSIM_DROP_THRESHOLD = 0.05
MAX_SSIM_DROP_THRESHOLD = 0.30
DEFAULT_SSIM_DROP_THRESHOLD = 0.10
HISTOGRAM_BINS = 256
HISTOGRAM_THRESHOLD = 0.5
PIXEL_DIFF_THRESHOLD = 0.01
PIXEL_BINARY_THRESHOLD = 48

# Detection slider
DETECTION_SLIDER_STEPS = int((MAX_SSIM_DROP_THRESHOLD - MIN_SSIM_DROP_THRESHOLD) / 0.01)
DEFAULT_DETECTION_SLIDER_VALUE = int(((0.25 - MIN_SSIM_DROP_THRESHOLD) / (MAX_SSIM_DROP_THRESHOLD - MIN_SSIM_DROP_THRESHOLD)) * DETECTION_SLIDER_STEPS)

# UI colors
VIDEO_BACKGROUND_COLOR = "#000000"  # Black
BOARD_BACKGROUND_COLOR = "#2e2e2e"  # Dark gray

# Debug
PRINT_DEFAULT_COLOR = '\033[0m'
PRINT_GRAY_COLOR = '\033[90m'
PRINT_RED_COLOR = '\033[91m'  # red
PRINT_GREEN_COLOR = '\033[92m'  # green
PRINT_YELLOW_COLOR  = '\033[93m'  # yellow
PRINT_CYAN_COLOR = '\033[96m'  # cyan


# Decorator
LOG_FUNCTION_NAMES = False

def log_function_name(has_params=True, color=PRINT_DEFAULT_COLOR):
    def decorator(func):
        def wrapper(self, *args, **kwargs):
            if LOG_FUNCTION_NAMES:
                class_name = self.__class__.__name__
                function_name = func.__name__
                print(f"Calling function: {class_name}.{color}{function_name}{PRINT_DEFAULT_COLOR}")
            return func(self, *args, **kwargs) if has_params else func(self)
        return wrapper
    return decorator


class ShotBoard(QMainWindow):
    class ClickableScrollArea(QScrollArea):
        clicked = pyqtSignal()  # Signal to emit when the scroll area is clicked

        def mousePressEvent(self, event):
            self.clicked.emit()
            # Call the base class implementation to ensure normal behavior
            super().mousePressEvent(event)


    @log_function_name(color=PRINT_GREEN_COLOR)
    def __init__(self, geom=QRect(100, 100, 800, 600)):
        super().__init__()

        self._db = ShotBoardDb()
        self._db_filename = None
        self._history = CommandHistory()
        self._shot_widgets = []
        self._selection_index_first = None
        self._selection_index_last = None
        self._video_path = None
        self._fps = 0
        self._frame_width = 0
        self._frame_height = 0
        self._duration = 0
        self._start_frame_index = None
        self._frame_count = None

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
        splitter.setSizes([50, 50])
        splitter.setHandleWidth(SPLITTER_HANDLE_WIDTH)
        splitter.setStyleSheet(f"""
            QSplitter::handle {{
                background-color: darkgray;
            }}
            QSplitter::handle:vertical {{
                height: {SPLITTER_HANDLE_WIDTH}px;
            }}
            QSplitter::handle:horizontal {{
                width: {SPLITTER_HANDLE_WIDTH}px;
            }}
        """)
        # Add the splitter to the central layout
        central_layout.addWidget(splitter)

        # Set the central widget
        self.setCentralWidget(central_widget)

        QApplication.instance().installEventFilter(self)  # Install global event filter

        self.create_status_bar()
        self.statusBar().showMessage("Load a video.")
        self.update_ui_state()

        # timer to update slide bar
        self._update_timer = QTimer(self) 
        self._update_timer.timeout.connect(self.on_timer_timeout)

    ##
    ## MENU, MAIN WIDGETS
    ##
    

    @log_function_name()
    def create_status_bar(self):
        # Create the status bar
        self._status_bar = self.statusBar()

        # Add a QLabel for a static message (bottom left)
        #self.status_label = QLabel("Ready")
        #self._status_bar.addWidget(self.status_label)

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
        action.setShortcut(QKeySequence.New)
        action.triggered.connect(self.on_menu_new)
        file_menu.addAction(action)

        file_menu.addSeparator()

        # Create 'Open' action
        action = QAction('Open Video', self)
        action.setShortcut(QKeySequence.Open)
        action.triggered.connect(self.on_menu_open_video)
        file_menu.addAction(action)

        # Create 'Open' action
        action = QAction('Open Shot List', self)
        action.triggered.connect(self.on_menu_open_shotlist)
        file_menu.addAction(action)

        file_menu.addSeparator()

        # Create 'Save' action
        action = QAction('Save', self)
        action.setShortcut(QKeySequence.Save)
        action.triggered.connect(self.on_menu_save)
        file_menu.addAction(action)

        # Create 'Save As' action
        action = QAction('Save as', self)
        action.setShortcut(QKeySequence(Qt.SHIFT + Qt.CTRL + Qt.Key_S))
        action.triggered.connect(self.on_menu_save_as)
        file_menu.addAction(action)

        file_menu.addSeparator()

        # Create 'Exit' action
        action = QAction('Exit', self)
        action.triggered.connect(self.on_menu_exit)
        file_menu.addAction(action)

        #
        # Create an 'Edit' menu
        #
        edit_menu = menubar.addMenu('Edit')
        
        # Create an "Undo" action with Ctrl + Z shortcut
        action = QAction("Undo", self)
        action.setShortcuts([QKeySequence.Undo])  # QKeySequence("Ctrl+Z")
        action.triggered.connect(self._history.undo)
        edit_menu.addAction(action)
        action.setDisabled(True)

        # Create an "Redo" action with Ctrl + Z shortcut
        action = QAction("Redo", self)
        action.setShortcuts([QKeySequence.Redo])  # QKeySequence("Ctrl+Y"), QKeySequence("Shift+Ctrl+Z")
        action.triggered.connect(self._history.redo)
        edit_menu.addAction(action)
        action.setDisabled(True)

        edit_menu.addSeparator()

        # Create an "Redo" action with Ctrl + Z shortcut
        action = QAction("Select All", self)
        action.setShortcuts([QKeySequence.SelectAll])  # Ctrl+A
        action.triggered.connect(self.select_all)
        edit_menu.addAction(action)
        #action.setDisabled(True)

        # Create an "Redo" action with Ctrl + Z shortcut
        action = QAction("Deselect All", self)
        action.setShortcuts([QKeySequence("Ctrl+D")])
        action.triggered.connect(self.deselect_all)
        edit_menu.addAction(action)
        #action.setDisabled(True)


    @log_function_name()
    def create_top_widget(self):
        top_widget = QWidget()

        # Create a vertical layout for the top part
        top_layout = QVBoxLayout()
        top_widget.setLayout(top_layout)
        margins = top_layout.contentsMargins()
        top_layout.setContentsMargins(0, margins.top(), 0, margins.bottom())

        # Create a media player object
        self._media_player = QMediaPlayer(None, QMediaPlayer.VideoSurface)
        #self._media_player.positionChanged.connect(self.on_mediaplayer_position_changed)
        #self._media_player.durationChanged.connect(self.on_mediaplayer_duration_changed)
        self._media_player.stateChanged.connect(self.on_mediaplayer_state_changed)
        self._media_player.error.connect(self.on_media_player_error)

        # Create a video widget for displaying video output
        self._video_widget = QVideoWidget()
        self._video_widget.setStyleSheet(f"background-color: {VIDEO_BACKGROUND_COLOR};")
        self._media_player.setVideoOutput(self._video_widget)
        self._video_widget.mousePressEvent = self.on_video_widget_clicked
        top_layout.addWidget(self._video_widget)

        # Create a horizontal layout for the slider
        slider_layout = QHBoxLayout()
        top_layout.addLayout(slider_layout)

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

        # Create an edge detection checkbox with a label
        self._edgedetect_checkbox = QCheckBox("Lines")  
        self._edgedetect_checkbox.toggled.connect(self.on_edge_detection_toggled)
        self._edgedetect_checkbox.setStatusTip("Check to apply 'Sobel' edge detection to the thumbnails.")
        self._edgedetect_checkbox.setChecked(False)
        ShotWidget.detect_edges = False
        button_layout.addWidget(self._edgedetect_checkbox)

        # Create an edge factor spinbox
        self._edgefactor_spinbox = QSpinBox()
        self._edgefactor_spinbox.setRange(1, 10)
        self._edgefactor_spinbox.valueChanged.connect(self.on_edge_factor_changed)
        self._edgefactor_spinbox.setStatusTip("Set the contrast factor of the 'Sobel' edge detection algorhythm.")
        self._edgefactor_spinbox.setValue(1)
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

        # Detection level slider
        self._detection_slider = QSlider(Qt.Horizontal)
        self._detection_slider.setRange(0, DETECTION_SLIDER_STEPS)
        self._detection_slider.setValue(DEFAULT_DETECTION_SLIDER_VALUE)
        #self._detection_slider.mousePressEvent = self.on_detection_slider_click
        self._detection_slider.sliderMoved.connect(self.on_detection_slider_moved)
        self._detection_slider.setFixedWidth(100)

        # Detection label
        self._detection_label = QLabel(f"{self.convert_detection_slider_value_to_ssim_drop_threshold(self._detection_slider.value()):.2f}")
        self._detection_label.setAlignment(Qt.AlignRight | Qt.AlignVCenter)

        # Create a downscale spinbox
        self._downscale_spinbox = QSpinBox()
        self._downscale_spinbox.setStatusTip("Set the width of the downscaled image size to ease shot detection.")
        self._downscale_spinbox.setRange(64, 1280)
        self._downscale_spinbox.setSingleStep(64)
        self._downscale_spinbox.setValue(128)

        # Create a layout for the detection widgets
        detection_layout = QHBoxLayout()
        detection_layout.addStretch()  # Add stretch to push elements to the right
        detection_layout.addWidget(self._scan_button)
        detection_layout.addWidget(self._plot_checkbox)
        detection_layout.addWidget(self._detection_slider)
        detection_layout.addWidget(self._detection_label)
        detection_layout.addWidget(self._downscale_spinbox)
        detection_layout.setSpacing(5)  # Adjust spacing between label and slider

        button_layout.addStretch()
        button_layout.addLayout(detection_layout)

        # Create a merge button
        self._merge_button = QPushButton('Merge selected shots')
        self._merge_button.clicked.connect(self.on_merge_button_clicked)
        self._merge_button.setStyleSheet(f"background-color: {SHOT_WIDGET_SELECT_COLOR};")
        self._merge_button.setStatusTip("Merge the selected shots as one shot (in case they were incorrectly detected as separate shots).")
        button_layout.addStretch()
        button_layout.addWidget(self._merge_button)

        # Volume label
        volume_label = QLabel("Volume")
        volume_label.setAlignment(Qt.AlignRight | Qt.AlignVCenter)

        # Volume slider
        self._volume_slider = QSlider(Qt.Horizontal)
        self._volume_slider.setRange(0, 100)
        self._volume_slider.setValue(75)
        self._volume_slider.mousePressEvent = self.on_volume_slider_click
        self._volume_slider.sliderMoved.connect(self.on_volume_slider_moved)
        self._volume_slider.setFixedWidth(150)
        ShotWidget.volume = self._volume_slider.value() / 100

        # Create a layout for the volume slider and label
        volume_layout = QHBoxLayout()
        volume_layout.addStretch()  # Add stretch to push elements to the right
        volume_layout.addWidget(volume_label)
        volume_layout.addWidget(self._volume_slider)
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
        bottom_layout.setContentsMargins(0, margins.top(), 0, margins.bottom())

        scrollarea_layout = QHBoxLayout()
        bottom_layout.addLayout(scrollarea_layout)

        # Wrap the grid layout in a scroll area
        self._scroll_area = self.ClickableScrollArea()
        self._scroll_area.setWidgetResizable(True)
        self._scroll_area.setStatusTip("Click on a shot to play.")
        self._scroll_area.setStyleSheet(f"background-color: {BOARD_BACKGROUND_COLOR};")
        self._scroll_area.clicked.connect(self.deselect_all)
        scrollarea_layout.addWidget(self._scroll_area)

        # Create a container widget to hold the grid layout
        grid_widget = QWidget()
        self._grid_layout = QGridLayout()
        self._grid_layout.setAlignment(Qt.AlignTop | Qt.AlignLeft)
        grid_widget.setLayout(self._grid_layout)
        self._scroll_area.setWidget(grid_widget)

        return bottom_widget


    ##
    ## SLOTS
    ##


    @log_function_name(has_params=False, color=PRINT_GREEN_COLOR)
    def on_menu_new(self):
        self.reset_all()


    @log_function_name(has_params=False, color=PRINT_GREEN_COLOR)
    def on_menu_open_video(self):
        file_dialog = QFileDialog()
        file_dialog.setAcceptMode(QFileDialog.AcceptOpen)
        file_dialog.setNameFilter("Video files (*.mp4 *.avi *.mkv)")
        if file_dialog.exec_() == QFileDialog.Accepted:
            self.set_video(file_dialog.selectedUrls()[0])


    @log_function_name(has_params=False, color=PRINT_GREEN_COLOR)
    def on_menu_open_shotlist(self):
        if not self._video_path:
            QMessageBox.warning(self, "Warning", "Please load a video first.")
            return
        options = QFileDialog.Options()
        filename, _ = QFileDialog.getOpenFileName(self, "Open Shot File", None, "Shot Files (*.json);;All Files (*)", options=options)
        if filename:
            self._db.load_from_json(filename)
            self._db_filename = filename
            #self.add_filename_to_recent(filename)  # update 'Open Recent' menu.
            if self._frame_count == self._db.get_frame_count():
                self.create_and_display_shot_widgets()
            else:
                QMessageBox.warning(self, "Warning", "The shot list does not match the video.")
                self._db.clear_shots()
        self.update_window_title()


    @log_function_name(has_params=False, color=PRINT_GREEN_COLOR)
    def on_menu_save(self):
        if self._db_filename:
            self._db.save_to_json(self._db_filename)
        else:
            self.on_menu_save_as()
        self.update_window_title()


    @log_function_name(has_params=False, color=PRINT_GREEN_COLOR)
    def on_menu_save_as(self):
        options = QFileDialog.Options()
        filename, _ = QFileDialog.getSaveFileName(self, "Save Shot File", self._db_filename, "Shot Files (*.json);;All Files (*)", options=options)
        if filename:
            #if os.path.exists(filename) and not QMessageBox.question(self, 'File Exists', f"The file {filename} already exists. Do you want to overwrite it?", QMessageBox.Yes | QMessageBox.No, QMessageBox.No) == QMessageBox.Yes:
            #    return
            self._db.save_to_json(filename)
            self._db_filename = filename
            # self.push_filename_to_recent(filename)  # update 'Open Recent' menu.
            self.update_window_title()


    @log_function_name(has_params=False, color=PRINT_GREEN_COLOR)
    def on_menu_exit(self):
        reply = QMessageBox.question(self, 'Confirm Exit', 'Exit?', QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
        if reply == QMessageBox.Yes:
            self.close()


    @log_function_name(color=PRINT_GREEN_COLOR)
    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.update_grid_layout()


    #@log_function_name()
    def eventFilter(self, obj, event):
        if event.type() == QEvent.KeyPress:
            if event.key() == Qt.Key_Space:
                self._play_button.click()
                return True

            modifiers = event.modifiers()
            if modifiers & Qt.ShiftModifier:
                frame_inc = 4  # 4 frames
            elif modifiers & Qt.ControlModifier:
                frame_inc = self._fps  # 1 second
            elif modifiers & Qt.AltModifier:
                frame_inc = 4 * self._fps  # 4 seconds
            else:
                frame_inc = 1

            frame_index = self.qtvid_pos_to_frame_index()

            if event.key() == Qt.Key_Right:
                self.set_qtvid_pos_to_mid_frame(frame_index + frame_inc)
                return True
            elif event.key() == Qt.Key_Left:
                self.set_qtvid_pos_to_mid_frame(frame_index - frame_inc)
                return True

        # Unprocessed events propagate as usual
        return super().eventFilter(obj, event)


    @log_function_name(color=PRINT_GREEN_COLOR)
    def on_handle_splitter_moved(self, pos, index):
        self.update_grid_layout()


    @log_function_name(has_params=False, color=PRINT_GREEN_COLOR)
    def on_video_widget_clicked(self):
        if not self._video_path:
            return
        self.on_play_button_clicked()


    @log_function_name(has_params=False, color=PRINT_GREEN_COLOR)
    def on_play_button_clicked(self):
        if not self._video_path:
            return
        player_state = self._media_player.state()
        if player_state == QMediaPlayer.StoppedState:
            self.play_video()
        elif player_state == QMediaPlayer.PlayingState:
            self.pause_video()
        elif player_state == QMediaPlayer.PausedState:
            self.resume_video()


    @log_function_name(has_params=False, color=PRINT_GREEN_COLOR)
    def on_stop_button_clicked(self):
        self.stop_video()


    @log_function_name(color=PRINT_GREEN_COLOR)
    def on_edge_detection_toggled(self, checked):
        ShotWidget.detect_edges = checked


    @log_function_name(color=PRINT_GREEN_COLOR)
    def on_edge_factor_changed(self, value):
        ShotWidget.edge_factor = value


    @log_function_name(has_params=False, color=PRINT_GREEN_COLOR)
    def on_split_button_clicked(self):
        self.split_video()


    @log_function_name(has_params=False, color=PRINT_GREEN_COLOR)
    def on_merge_button_clicked(self):
        self.merge_selected_shots()


    @log_function_name(has_params=False, color=PRINT_GREEN_COLOR)
    def on_scan_button_clicked(self):
        shot_widget = self.merge_selected_shots()
        if shot_widget == None:
            return
        self.detect_shots_ssim(shot_widget.get_start_frame_index(), shot_widget.get_end_frame_index())

        #if self._media_player.isMetaDataAvailable():
        #    fps = self._media_player.metaData("VideoFrameRate")
        #    print(fps)
        #qtvid_pos = round(self.convert_frame_index_to_qtvid_pos(self._start_frame_index))
        #qtvid_pos = round(self.convert_frame_index_to_qtvid_pos(1))
        ##self._media_player.setPosition(1)  # first qtvid_pos of frame #1
        #qtvid_pos = round(1000 / self._fps) - 1  # last qtvid_pos of frame #1 (= 1 frame before frame #2)
        #self._media_player.setPosition(qtvid_pos)
        #print(f"\n<<<<<<<<<<<<<<<<<<< position = {self._media_player.position()} >>>>>>>>>>>>>>>>>>>>\n\n")
        #self.update_slider_and_spinbox(self._start_frame_index)


    @log_function_name(color=PRINT_GREEN_COLOR)
    def on_detection_slider_moved(self, value):
        ssim_drop_threshold = self.convert_detection_slider_value_to_ssim_drop_threshold(value)
        self._detection_label.setText(f"{ssim_drop_threshold:.2f}")


    @log_function_name(color=PRINT_GREEN_COLOR)
    def on_volume_slider_click(self, event):
        if event.button() == Qt.LeftButton:
            volume = int(self._volume_slider.minimum() + ((self._volume_slider.maximum() - self._volume_slider.minimum()) * event.x()) / self._volume_slider.width())
            ShotWidget.volume = volume / 100
            self._media_player.setVolume(volume)
            event.accept()
        QSlider.mousePressEvent(self._volume_slider, event)


    @log_function_name(color=PRINT_GREEN_COLOR)
    def on_volume_slider_moved(self, volume):
        ShotWidget.volume = volume / 100
        self._media_player.setVolume(volume)


    @log_function_name(color=PRINT_GREEN_COLOR)
    def on_seek_slider_click(self, event):
        if event.button() == Qt.LeftButton:
            frame_index = int(self._seek_slider.minimum() + ((self._seek_slider.maximum() - self._seek_slider.minimum()) * event.x()) / self._seek_slider.width())
            self.set_qtvid_pos_to_mid_frame(frame_index)
            event.accept()
        QSlider.mousePressEvent(self._seek_slider, event)


    @log_function_name(color=PRINT_GREEN_COLOR)
    def on_seek_slider_moved(self, frame_index):
        self.set_qtvid_pos_to_mid_frame(frame_index)


    @log_function_name(color=PRINT_GREEN_COLOR)
    def on_seek_spinbox_changed(self, frame_index):
        self.set_qtvid_pos_to_mid_frame(frame_index)


    @log_function_name()
    def on_mediaplayer_state_changed(self, state):
        if state == QMediaPlayer.PlayingState:
            self._play_button.setIcon(self.style().standardIcon(QStyle.SP_MediaPause))
        else:
            self._play_button.setIcon(self.style().standardIcon(QStyle.SP_MediaPlay))


    @log_function_name(has_params=False, color=PRINT_RED_COLOR)
    def on_media_player_error(self):
        print(f"{PRINT_RED_COLOR}{self._media_player.errorString()}{PRINT_DEFAULT_COLOR}")


    @log_function_name(color=PRINT_GREEN_COLOR)
    def on_shot_widget_clicked(self, shift_pressed):
        # Fetch the shot widget emitting the signal
        shot_widget = self.sender()
        assert shot_widget
        shot_index = self._shot_widgets.index(shot_widget)

        if shift_pressed:  # Extends or reduce the selection
            self.extend_shot_widget_selection(shot_index)
        else:  # Select one shot
            self.select_shot_widget(shot_index)

        if self._update_timer.isActive():
            self._update_timer.stop()
        self._media_player.pause()
        self._start_frame_index = shot_widget.get_start_frame_index()  # integer
        qtvid_pos = round(self.convert_frame_index_to_qtvid_pos(self._start_frame_index))
        self._media_player.setPosition(qtvid_pos)
        self.update_slider_and_spinbox(self._start_frame_index)


    @log_function_name(has_params=False, color=PRINT_YELLOW_COLOR)
    def on_timer_timeout(self):
        frame_index = self.qtvid_pos_to_frame_index()  # float
        self.update_slider_and_spinbox(frame_index)


    ##
    ## UPDATES
    ##
   

    @log_function_name()
    def update_status_bar(self):
        duration_hms = str(datetime.timedelta(seconds=int(self._duration)))
        duration_h = self._duration / 3600 if self._duration > 0 else 1  # Prevent division by zero
        shots_per_hour = round(len(self._db) / duration_h)

        self._info_label.setText(
            f"FPS: {self._fps:.3f} | "
            f"Resolution: {self._frame_width}x{self._frame_height} | "
            f"Duration: {duration_hms} | "
            f"Shots: {len(self._db)} | "
            f"Shots/hour: {shots_per_hour}"
        )


    # Ne cause pas de réactions en chaîne.
    @log_function_name()
    def update_window_title(self):
        title = DEFAULT_TITLE
        if self._video_path:
            title += f" - {self._video_path}"
        else:
            title += " - (undefined)"
    
        # Add '*' if database has unsaved changes
        if self._db.is_dirty():
            title += " *"

        self.setWindowTitle(title)

    
    def update_ui_state(self):
        enabled = (self._video_path != None)
        self._seek_slider.setEnabled(enabled)
        self._seek_spinbox.setEnabled(enabled)
        self._play_button.setEnabled(enabled)
        self._stop_button.setEnabled(enabled)
        self._split_button.setEnabled(enabled)
        self._scan_button.setEnabled(not self.is_selection_empty())
        self._merge_button.setEnabled(not self.is_selection_empty())
        #self._detection_slider.setEnabled(not self.is_selection_empty())


    def update_slider_and_spinbox(self, frame_index):
        self._seek_slider.blockSignals(True)
        self._seek_spinbox.blockSignals(True)

        self._seek_slider.setValue(int(frame_index))
        self._seek_spinbox.setValue(int(frame_index))

        self._seek_slider.blockSignals(False)
        self._seek_spinbox.blockSignals(False)


    def reset_all(self):
        self.stop_video()
        self._media_player.setPosition(0)
        self.update_slider_and_spinbox(0)
        self.clear_shot_widgets()
        self._db.clear_shots()
        self._db_filename = None
        self._video_path = None
        self._fps = 0
        self.update_ui_state()
        self.update_window_title()
        self.statusBar().showMessage("Load a video.")


    def clear_shot_widgets(self):
        self.deselect_all()
        for widget in self._shot_widgets:
            self._grid_layout.removeWidget(widget)
            widget.hide()
            widget.deleteLater()
        self._shot_widgets = []
        self._selected_shot_widget = None


    def create_shot_widget(self, frame_index):
        shot_widget = ShotWidget(self._video_path, self._fps, *self._db.get_start_end_frame_indexes(frame_index))
        shot_widget.clicked.connect(self.on_shot_widget_clicked)
        return shot_widget


    def create_and_display_shot_widgets(self):
        self._shot_widgets = []
        self._selected_shot_widget = None

        # Create a progress dialog
        progress_dialog = QProgressDialog("Creating shots...", "Cancel", 0, len(self._db), self)
        progress_dialog.setWindowModality(Qt.WindowModal)
        progress_dialog.setWindowTitle("Shot creation")
        progress_dialog.setWindowFlags(progress_dialog.windowFlags() & ~Qt.WindowContextHelpButtonHint)
        progress_dialog.setMinimumDuration(0)
        progress_dialog.setValue(0)

        for i, frame_index in enumerate(self._db):
            if progress_dialog.wasCanceled():
                break
            shot_widget = self.create_shot_widget(frame_index)
            self._shot_widgets.append(shot_widget)
            progress_dialog.setValue(i + 1)

        progress_dialog.close()
        self.update_grid_layout()
        self.update_status_bar()
        self.update_window_title()


    @log_function_name()
    def update_grid_layout(self):
        # Evaluate the number of columns based on the available width and cell size.
        left_margin, top_margin, right_margin, bottom_margin = self._grid_layout.getContentsMargins()
        horz_spacing, vert_spacing = self._grid_layout.horizontalSpacing(), self._grid_layout.verticalSpacing()

        available_width = self._scroll_area.width() - (left_margin + right_margin) - 2 # size of the framing itself
        cell_size = SHOT_WIDGET_WIDTH
        if available_width < cell_size:
            num_cols = 1
        else:
            num_cols = 1 + (available_width - cell_size) // (cell_size + horz_spacing)

        # Add the widgets
        for idx, shot_widget in enumerate(self._shot_widgets):
            row = idx // num_cols
            col = idx % num_cols
            self._grid_layout.addWidget(shot_widget, row, col, 1, 1)

        # Remove and delete all widgets in the layout that are not in self._shot_widgets
        widgets_to_remove = [
            self._grid_layout.itemAt(i).widget()
            for i in range(self._grid_layout.count())
            if self._grid_layout.itemAt(i).widget() not in self._shot_widgets
        ]
        for widget in widgets_to_remove:
            self._grid_layout.removeWidget(widget)
            widget.hide()
            widget.deleteLater()

        self.update_status_bar()


    ##
    ## ACTIONS
    ##


    @log_function_name(color=PRINT_YELLOW_COLOR)
    def set_video(self, url):
        assert url
        if not url:
            return
        
        self._video_path = url.toString().replace('file:///', '')
        self.update_window_title()

        cap = cv2.VideoCapture(self._video_path)
        self._fps = cap.get(cv2.CAP_PROP_FPS)
        self._frame_count = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
        self._frame_width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
        self._frame_height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
        self._duration = self._frame_count / self._fps if self._fps > 0 else 0  # in seconds
        cap.release()

        self._db.clear_shots()
        self._db.set_shot_count(self._frame_count)

        media_content = QMediaContent(url)
        self._media_player.setMedia(media_content)  # /!\ asynchronous
        #self._media_player.setNotifyInterval(int(1/self._fps))
        self._seek_slider.setRange(0, self._frame_count - 1)
        self._seek_spinbox.setRange(0, self._frame_count - 1)
        self._start_frame_index = 0
        self._media_player.setVolume(self._volume_slider.value())
        self._media_player.pause()

        self._db.add_shot(0)  # Add a single shot covering the whole video
        self.clear_shot_widgets()
        shot_widget = self.create_shot_widget(0)
        self._shot_widgets.append(shot_widget)

        self.update_grid_layout()
        self.update_status_bar()
        self.update_window_title()

        self.select_shot_widget(0)
        self.update_ui_state()


    @log_function_name(color=PRINT_YELLOW_COLOR)
    def set_qtvid_pos_to_mid_frame(self, frame_index):  # frame index as a float (e.g. 23.5)
        self.pause_video()
        self.update_slider_and_spinbox(frame_index)
        qtvid_pos = round(self.convert_frame_index_to_qtvid_pos(frame_index))
        self._media_player.setPosition(qtvid_pos)


    @log_function_name(color=PRINT_YELLOW_COLOR)
    def start_update_timer(self):
        self._update_timer.start(UPDATE_TIMER_INTERVAL)


    @log_function_name(color=PRINT_YELLOW_COLOR)
    def stop_update_timer(self):
        if self._update_timer.isActive():
            self._update_timer.stop()
            self.on_timer_timeout()


    @log_function_name(color=PRINT_YELLOW_COLOR)
    def play_video(self):
        assert self._media_player
        if not self._media_player:
            return

        player_state = self._media_player.state()
        if player_state == QMediaPlayer.PlayingState:
            self.pause_video()

        self._media_player.play()
        self.start_update_timer()


    @log_function_name(color=PRINT_YELLOW_COLOR)
    def pause_video(self):
        assert self._media_player
        if not self._media_player:
            return
        
        self.stop_update_timer()
        self._media_player.pause()


    @log_function_name(color=PRINT_YELLOW_COLOR)
    def resume_video(self):
        assert self._media_player
        if not self._media_player:
            return
        
        self._media_player.play()
        self.start_update_timer()


    @log_function_name(color=PRINT_YELLOW_COLOR)
    def stop_video(self):
        assert self._media_player
        if not self._media_player:
            return
        
        self.stop_update_timer()
        self._media_player.stop()

    
    @log_function_name()
    def detect_shots_ssim(self, start_frame_index, end_frame_index):
        self._media_player.pause()
        self._media_player.setPosition(round(self.convert_frame_index_to_qtvid_pos(start_frame_index)))

        assert self._video_path
        if not self._video_path:
            return

        # Get video properties
        probe = ffmpeg.probe(self._video_path)
        video_info = next(stream for stream in probe['streams'] if stream['codec_type'] == 'video')
        frame_width = int(video_info['width'])
        frame_height = int(video_info['height'])

        # Define target width and compute target height while maintaining aspect ratio
        TARGET_WIDTH = self._downscale_spinbox.value()
        TARGET_HEIGHT = round(frame_height * (TARGET_WIDTH / frame_width))
        FRAME_SIZE = TARGET_WIDTH * TARGET_HEIGHT

        # Convert frame index to timestamp (in seconds) for FFmpeg seeking
        start_frame_time = int(start_frame_index) / self._fps  # frame position in seconds

        # FFmpeg command to extract frames as grayscale
        ffmpeg_command = [
            "ffmpeg",
            #"-loglevel", "debug",
            "-ss", str(start_frame_time),  # Fast seek FIRST
            "-i", self._video_path,  # Input file AFTER
            "-vframes", str(end_frame_index - start_frame_index),
            "-vf", f"scale={TARGET_WIDTH}:{TARGET_HEIGHT}, format=gray",  # Scale and convert to grayscale
            "-f", "rawvideo",
            "-pix_fmt", "gray",
            #"-accurate_seek",  # Ensures exact frame accuracy
            "-nostdin",
            "-"
        ]
        process = subprocess.Popen(ffmpeg_command, stdout=subprocess.PIPE, stderr=subprocess.PIPE)

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
            x_data = np.array([], dtype=int)  # Frame indices
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
                    # Detect a spike (sudden drop followed by rise)
                    if ((prev_prev_ssim - prev_ssim >= ssim_drop_threshold and current_ssim - prev_ssim >= ssim_drop_threshold) or
                        (prev_prev_ssim - prev_ssim >= MAX_SSIM_DROP_THRESHOLD and  current_ssim - prev_ssim >= MIN_SSIM_DROP_THRESHOLD)):
                        # Add shot at the previous frame
                        cut_frame_index = frame_index - 1
                        shot_index = self._db.add_shot(cut_frame_index)
                        self._media_player.setPosition(round(self.convert_frame_index_to_qtvid_pos(cut_frame_index)))

                        # Insert a new shot widget
                        new_shot_widget = ShotWidget(self._video_path, self._fps, cut_frame_index, end_frame_index)
                        new_shot_widget.clicked.connect(self.on_shot_widget_clicked)
                        self._shot_widgets.insert(shot_index, new_shot_widget)

                        # Update the end frame of the previous shot widget
                        if shot_index > 0:
                            prev_shot_widget = self._shot_widgets[shot_index - 1]
                            prev_shot_widget.set_end_frame_index(cut_frame_index, False)

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
        process.stderr.close()
        process.wait()
        progress_dialog.close()

        # Display the total time taken to detect shots
        total_time_hms = QTime(0, 0).addMSecs(start_timer.elapsed()).toString("hh:mm:ss")
        #print(f"Shots detected in {total_time_hms}")
        start_timer.invalidate()

        # Close the graph if it's still open
        if plot_enabled and plt.fignum_exists(fig.number):
            plt.ioff()  # Turn off interactive mode when done
            plt.show()  # Show final graph

        # Update the grid layout
        self.update_grid_layout()
        self.update_status_bar()
        self.update_window_title()


    @log_function_name(color=PRINT_GREEN_COLOR)
    def split_video(self):
        assert self._shot_widgets
        if not self._shot_widgets:
            return
        
        self.deselect_all()

        # Get the current frame and evaluate the start and end frames of the shot
        cut_frame_index = self._seek_spinbox.value()  # integer
        start_frame_index, end_frame_index = self._db.get_start_end_frame_indexes(cut_frame_index)  # integers
        if cut_frame_index <= start_frame_index or cut_frame_index >= end_frame_index - 1:
            return

        # Insert a new frame in the database
        shot_index = self._db.add_shot(cut_frame_index)

        # Insert a new shot widget
        new_shot_widget = ShotWidget(self._video_path, self._fps, cut_frame_index, end_frame_index)
        new_shot_widget.clicked.connect(self.on_shot_widget_clicked)
        self._shot_widgets.insert(shot_index, new_shot_widget)

        # Update the end frame of the previous shot widget
        prev_shot_widget = self._shot_widgets[shot_index - 1]
        prev_shot_widget.set_end_frame_index(cut_frame_index)

        # Update the grid layout
        self.update_grid_layout()
        self.update_status_bar()
        self.update_window_title()

        self.select_shot_widget(shot_index)


    @log_function_name(color=PRINT_GREEN_COLOR)
    def merge_selected_shots(self):
        assert self._shot_widgets
        if not self._shot_widgets:
            return None

        if self.is_selection_empty():
            return None

        shot_index_min, shot_index_max = self.get_selection_index_min_max()
        shot_widget_min = self._shot_widgets[shot_index_min]
        shot_widget_max = self._shot_widgets[shot_index_max]
        start_frame_index = shot_widget_min.get_start_frame_index()
        end_frame_index = shot_widget_max.get_end_frame_index()

        shot_widget_min.set_end_frame_index(end_frame_index)

        self.deselect_all()

        widgets_to_remove = self._shot_widgets[shot_index_min + 1 : shot_index_max + 1]  # shot_index_max + 1 is excluded
        for widget in widgets_to_remove:
            self._grid_layout.removeWidget(widget)
            widget.hide()
            widget.deleteLater()
        del self._shot_widgets[shot_index_min + 1 : shot_index_max + 1]

        del self._db[shot_index_min + 1 : shot_index_max + 1]
        
        # Update the grid layout
        self.update_grid_layout()
        self.update_status_bar()
        self.update_window_title()

        # Reselect the first shot widget
        self.select_shot_widget(shot_index_min)
        self._media_player.setPosition(round(self.convert_frame_index_to_qtvid_pos(start_frame_index)))

        return shot_widget_min


    ##
    ## SELECTION
    ##


    # @log_function_name(color=PRINT_GRAY_COLOR)
    def is_selection_empty(self):
        return self._selection_index_first == None


    # @log_function_name(color=PRINT_GRAY_COLOR)
    def select_all(self):
        self._selection_index_first = 0
        self._selection_index_last = len(self._shot_widgets) - 1
        for idx, shot_widget in enumerate(self._shot_widgets):
            shot_widget.set_selected(True)
        self.update_ui_state()


    # @log_function_name(color=PRINT_GRAY_COLOR)
    def deselect_all(self):
        #for idx, shot_widget in enumerate(self._shot_widgets):
        #    shot_widget.set_selected(False)
        if self._selection_index_first != None:
            idx_min, idx_max = self.get_selection_index_min_max()
            for i in range(idx_min, idx_max + 1):
                self._shot_widgets[i].set_selected(False)
        self._selection_index_first = None
        self._selection_index_last = None
        self.update_ui_state()


    # @log_function_name(color=PRINT_GRAY_COLOR)
    def select_shot_widget(self, shot_index):
        self.deselect_all()
        self._selection_index_first = shot_index
        self._selection_index_last = shot_index
        self._shot_widgets[shot_index].set_selected(True)
        self.update_ui_state()


    # @log_function_name(color=PRINT_GRAY_COLOR)
    def extend_shot_widget_selection(self, shot_index):
        # If the new index doesn't change the extented selection, do nothing
        if shot_index == self._selection_index_last:
            return

        # If the selection is empty, select from 0 to the new index
        if self._selection_index_first == None:
            assert self._selection_index_last == None
            for i in range(0, shot_index + 1):
                self._shot_widgets[i].set_selected(True)
            self._selection_index_first = 0
            self._selection_index_last = shot_index
            self.update_ui_state()
            return

        # Else either extend or reduce the selection
        if self._selection_index_first <= self._selection_index_last:
            if shot_index < self._selection_index_first:  # extend the selection to the left
                for i in range(shot_index, self._selection_index_first):
                    self._shot_widgets[i].set_selected(True)
                for i in range(self._selection_index_first + 1, self._selection_index_last + 1):
                    self._shot_widgets[i].set_selected(False)
            elif shot_index < self._selection_index_last:  # reduce the selection
                for i in range(shot_index + 1, self._selection_index_last + 1):
                    self._shot_widgets[i].set_selected(False)
            else:  # extend the selection to the right
                for i in range(self._selection_index_last + 1, shot_index + 1):
                    self._shot_widgets[i].set_selected(True)
        else:  # inverted min and max
            if shot_index > self._selection_index_first:  # extend the selection to the right
                for i in range(self._selection_index_last, self._selection_index_first):
                    self._shot_widgets[i].set_selected(False)
                for i in range(self._selection_index_first + 1, shot_index + 1):
                    self._shot_widgets[i].set_selected(True)
            elif shot_index > self._selection_index_last:  # reduce the selection
                for i in range(self._selection_index_last, shot_index):
                    self._shot_widgets[i].set_selected(False)
            else:  # extend the selection to the left
                for i in range(shot_index, self._selection_index_last):
                    self._shot_widgets[i].set_selected(True)

        self._selection_index_last = shot_index
        self.update_ui_state()


    #@log_function_name(color=PRINT_GRAY_COLOR)
    def get_selection_index_min_max(self):
        if self._selection_index_first == None:
            return None, None
        
        if self._selection_index_last == None:
            return self._selection_index_first, None
        
        if self._selection_index_first <= self._selection_index_last:
            return self._selection_index_first, self._selection_index_last
        else:
            return self._selection_index_last, self._selection_index_first


    ##
    ## HELPER
    ##


    #@log_function_name(color=PRINT_GRAY_COLOR)
    def convert_frame_index_to_qtvid_pos(self, frame_index):
        assert self._fps > 0
        offset = int(frame_index) - 0.5
        return offset * 1000 / self._fps if offset > 0 else 0  # returns mid-frame position in milliseconds


    #@log_function_name(color=PRINT_GRAY_COLOR)
    def qtvid_pos_to_frame_index(self):
        assert self._fps > 0
        qtvid_pos = self._media_player.position()
        return (qtvid_pos * 0.001 * self._fps) + 1  # returns a frame index as a float value (e.g. 13.5)


   #@log_function_name(color=PRINT_GRAY_COLOR)
    def convert_detection_slider_value_to_ssim_drop_threshold(self, value):
        return (MAX_SSIM_DROP_THRESHOLD - MIN_SSIM_DROP_THRESHOLD) * (value / DETECTION_SLIDER_STEPS) + MIN_SSIM_DROP_THRESHOLD


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
    window = ShotBoard(QRect(100, 100, 1024, 800))
    window.show()

    sys.exit(app.exec_())
