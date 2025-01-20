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

import cv2
import numpy as np
#from skimage.metrics import structural_similarity as ssim
import os
from PyQt5.QtCore import Qt, pyqtSignal, QRect, QTimer
from PyQt5.QtWidgets import QApplication, QMainWindow, QWidget, QMessageBox, QFileDialog, QProgressDialog
from PyQt5.QtWidgets import QSplitter, QHBoxLayout, QVBoxLayout, QGridLayout, QScrollArea, QSlider, QSpinBox
from PyQt5.QtWidgets import QLabel, QPushButton, QToolButton
from PyQt5.QtWidgets import QAction, QStyle
from PyQt5.QtGui import QKeySequence, QIcon, QPalette, QColor
from PyQt5.QtMultimedia import QMediaPlayer, QMediaContent
from PyQt5.QtMultimediaWidgets import QVideoWidget
import sys

APP_VERSION = "0.1.1"

DEFAULT_TITLE = "ShotBoard"
SPLITTER_HANDLE_WIDTH = 3
UPDATE_TIMER_INTERVAL = 1000  # 1/2 s
HISTOGRAM_BINS = 256
HISTOGRAM_THRESHOLD = 0.5
HISTOGRAM_THRESHOLD_MIX = 0.2
PIXEL_DIFF_THRESHOLD = 0.01
PIXEL_DIFF_THRESHOLD_MIX = 0.1
PIXEL_BINARY_THRESHOLD = 48
SSIM_THRESHOLD = 0.5
BLACK_COLOR = "#000000"
DARK_GRAY_COLOR = "#2e2e2e"

# Debug
PRINT_DEFAULT_COLOR = '\033[0m'
PRINT_GRAY_COLOR = '\033[90m'
PRINT_RED_COLOR = '\033[91m'  # red
PRINT_GREEN_COLOR = '\033[92m'  # green
PRINT_YELLOW_COLOR  = '\033[93m'  # yellow
PRINT_CYAN_COLOR = '\033[96m'  # cyan


# Decorator
LOG_FUNCTION_NAMES = True

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
        self._selection_index_first = -1
        self._selection_index_last = -1
        self._video_path = None
        self._fps = 0
        self._start_frame = None
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

        self.installEventFilter(self)
        self.setFocusPolicy(Qt.StrongFocus)

        self.statusBar().showMessage("Load a video.")
        self.update_buttons_state()

        # timer to update slide bar
        self._update_timer = QTimer(self) 
        self._update_timer.timeout.connect(self.on_timer_timeout)

    ##
    ## MENU, MAIN WIDGETS
    ##
    
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
        
        # Create an "Redo" action with Ctrl + Z shortcut
        action = QAction("Redo", self)
        action.setShortcuts([QKeySequence.Redo])  # QKeySequence("Ctrl+Y"), QKeySequence("Shift+Ctrl+Z")
        action.triggered.connect(self._history.redo)
        edit_menu.addAction(action)

        edit_menu.addSeparator()

        # Create 'Scan shots' action
        action = QAction('Scan shots', self)
        action.triggered.connect(self.on_menu_scan_shots)
        edit_menu.addAction(action)


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
        self._video_widget.setStyleSheet(f"background-color: {BLACK_COLOR};")
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

        # Create a split button
        self._split_button = QPushButton('Insert new shot starting at this frame')
        self._split_button.clicked.connect(self.on_split_button_clicked)
        self._split_button.setStyleSheet(f"background-color: {SHOT_WIDGET_PROGRESSBAR_COLOR};")
        self._split_button.setStatusTip("Add a new shot to the list starting at current position (in case it has not been detected as the beginning of a shot).")
        button_layout.addStretch()
        button_layout.addWidget(self._split_button)

        # Create a merge button
        self._merge_button = QPushButton('Merge selected shots')
        self._merge_button.clicked.connect(self.on_merge_button_clicked)
        self._merge_button.setStyleSheet(f"background-color: {SHOT_WIDGET_SELECT_COLOR};")
        self._merge_button.setStatusTip("Merge the selected shots as one shot (in case they were incorrectly detected as separate shots).")
        button_layout.addStretch()
        button_layout.addWidget(self._merge_button)

        # Volume label
        volume_label = QLabel("Volume:")
        volume_label.setAlignment(Qt.AlignRight | Qt.AlignVCenter)

        # Volume slider
        self._volume_slider = QSlider(Qt.Horizontal)
        self._volume_slider.setRange(0, 100)
        self._volume_slider.setValue(75)
        self._volume_slider.mousePressEvent = self.on_volume_slider_click
        self._volume_slider.sliderMoved.connect(self.on_volume_slider_moved)
        self._volume_slider.setFixedWidth(150)

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
        self._scroll_area.setStyleSheet(f"background-color: {DARK_GRAY_COLOR};")
        self._scroll_area.clicked.connect(self.clear_shot_widget_selection)
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
        self._db.clear()
        self._db_filename = None


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
                self._db.clear()


    @log_function_name(has_params=False, color=PRINT_GREEN_COLOR)
    def on_menu_save(self):
        if self._db_filename:
            self._db.save_to_json(self._db_filename)
        else:
            self.on_menu_save_as()


    @log_function_name(has_params=False, color=PRINT_GREEN_COLOR)
    def on_menu_save_as(self):
        options = QFileDialog.Options()
        filename, _ = QFileDialog.getSaveFileName(self, "Save Shot File", self._db_filename, "Shot Files (*.json);;All Files (*)", options=options)
        if filename:
            if os.path.exists(filename) and not QMessageBox.question(self, 'File Exists', f"The file {filename} already exists. Do you want to overwrite it?", QMessageBox.Yes | QMessageBox.No, QMessageBox.No) == QMessageBox.Yes:
                return
            self._db.save_to_json(filename)
            self._db_filename = filename
            # self.push_filename_to_recent(filename)  # update 'Open Recent' menu.


    @log_function_name(has_params=False, color=PRINT_GREEN_COLOR)
    def on_menu_exit(self):
        reply = QMessageBox.question(self, 'Confirm Exit', 'Exit?', QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
        if reply == QMessageBox.Yes:
            self.close()


    @log_function_name(has_params=False, color=PRINT_GREEN_COLOR)
    def on_menu_scan_shots(self):
        self.detect_shots()


    @log_function_name(color=PRINT_GREEN_COLOR)
    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.update_grid_layout()


    @log_function_name()
    def eventFilter(self, obj, event):
        if event.type() == QEvent.KeyPress:
            if event.key() == Qt.Key_Space:
                self._play_button.click()
                return True

            modifiers = event.modifiers()
            if modifiers & Qt.ShiftModifier:
                frame_inc = int(self._fps)
            elif modifiers & Qt.ControlModifier:
                frame_inc = int(10 * self._fps)
            elif modifiers & Qt.AltModifier:
                frame_inc = int(60 * self._fps)
            else:
                frame_inc = 1

            frame = round(self.get_mediaplayer_position_as_frame())
            if event.key() == Qt.Key_Right:
                self.set_current_frame(frame + frame_inc)
                return True
            elif event.key() == Qt.Key_Left:
                self.set_current_frame(frame - frame_inc)
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


    @log_function_name(has_params=False, color=PRINT_GREEN_COLOR)
    def on_split_button_clicked(self):
        self.split_video()


    @log_function_name(has_params=False, color=PRINT_GREEN_COLOR)
    def on_merge_button_clicked(self):
        self.merge_selected_shots()


    @log_function_name(color=PRINT_GREEN_COLOR)
    def on_volume_slider_click(self, event):
        if event.button() == Qt.LeftButton:
            vol = int(self._seek_slider.minimum() + ((self._seek_slider.maximum() - self._seek_slider.minimum()) * event.x()) / self._seek_slider.width())
            self._media_player.setVolume(vol)
            event.accept()
        QSlider.mousePressEvent(self._volume_slider, event)


    @log_function_name(color=PRINT_GREEN_COLOR)
    def on_volume_slider_moved(self, volume):
        self._media_player.setVolume(volume)


    @log_function_name(color=PRINT_GREEN_COLOR)
    def on_seek_slider_click(self, event):
        if event.button() == Qt.LeftButton:
            frame = int(self._seek_slider.minimum() + ((self._seek_slider.maximum() - self._seek_slider.minimum()) * event.x()) / self._seek_slider.width())
            self.set_current_frame(frame)
            event.accept()
        QSlider.mousePressEvent(self._seek_slider, event)


    @log_function_name(color=PRINT_GREEN_COLOR)
    def on_seek_slider_moved(self, frame):
        self.set_current_frame(frame)


    @log_function_name(color=PRINT_GREEN_COLOR)
    def on_seek_spinbox_changed(self, frame):
        self.set_current_frame(frame)


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
        index = self._shot_widgets.index(shot_widget)

        if shift_pressed:  # Extends or reduce the selection
            self.extend_shot_widget_selection(index)
        else:  # Select one shot
            self.select_shot_widget(index)

        self._start_frame = shot_widget.get_start_frame()

        self.stop_video()
        self.play_video()
        self.pause_video()


    @log_function_name(has_params=False, color=PRINT_YELLOW_COLOR)
    def on_timer_timeout(self):
        frame = int(self.get_mediaplayer_position_as_frame())
        self.update_slider_and_spinbox(frame)


    ##
    ## UPDATES
    ##


    # Ne cause pas de réactions en chaîne.
    @log_function_name()
    def update_window_title(self):
        title = DEFAULT_TITLE
        if self._video_path:
            title += f" - {self._video_path}"
        else:
            title += " - (undefined)"
        self.setWindowTitle(title)
    
    
    def update_buttons_state(self):
        enabled = (self._video_path != None)
        self._play_button.setEnabled(enabled)
        self._stop_button.setEnabled(enabled)
        self._split_button.setEnabled(enabled)
        self._merge_button.setEnabled(not self.IsSelectionEmpty())


    def update_slider_and_spinbox(self, frame):
        self._seek_slider.blockSignals(True)
        self._seek_spinbox.blockSignals(True)

        self._seek_slider.setValue(frame)
        self._seek_spinbox.setValue(frame)

        self._seek_slider.blockSignals(False)
        self._seek_spinbox.blockSignals(False)


    def create_and_display_shot_widgets(self):
        self._shot_widgets = []
        self._selected_shot_widget = None

        # Create a progress dialog
        progress_dialog = QProgressDialog("Creating shots...", "Cancel", 0, len(self._db._frames), self)
        progress_dialog.setWindowModality(Qt.WindowModal)
        progress_dialog.setWindowTitle("Shot creation")
        progress_dialog.setWindowFlags(progress_dialog.windowFlags() & ~Qt.WindowContextHelpButtonHint)
        progress_dialog.setMinimumDuration(0)
        progress_dialog.setValue(0)

        for i, frame in enumerate(self._db._frames):
            if progress_dialog.wasCanceled():
                break
            shot_widget = ShotWidget(self._video_path, self._fps, *self._db.get_start_end_frames(frame))
            shot_widget.clicked.connect(self.on_shot_widget_clicked)
            self._shot_widgets.append(shot_widget)
            progress_dialog.setValue(i + 1)

        progress_dialog.close()
        self.update_grid_layout()


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
        cap.release()

        self._db.clear()
        self._db.set_frame_count(self._frame_count)

        media_content = QMediaContent(url)
        self._media_player.setMedia(media_content)  # /!\ asynchronous
        self._media_player.setNotifyInterval(int(1/self._fps))
        self._seek_slider.setRange(0, self._frame_count - 1)
        self._seek_spinbox.setRange(0, self._frame_count - 1)
        self._start_frame = 0
        self._media_player.setVolume(self._volume_slider.value())
        self._media_player.pause()

        self.update_buttons_state()


    @log_function_name(color=PRINT_YELLOW_COLOR)
    def set_current_frame(self, frame):
        self.update_slider_and_spinbox(frame)
        position = int(self.convert_frame_to_position(frame))
        self._media_player.setPosition(position)


    @log_function_name(color=PRINT_YELLOW_COLOR)
    def start_update_timer(self):
        self._update_timer.start(UPDATE_TIMER_INTERVAL)


    @log_function_name(color=PRINT_YELLOW_COLOR)
    def stop_update_timer(self):
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

        self.set_current_frame(self._start_frame)
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


    @log_function_name(color=PRINT_GREEN_COLOR)
    def split_video(self):
        assert self._shot_widgets
        if not self._shot_widgets:
            return

        # Get the current frame and evaluate the start and end frames of the shot
        cut_frame = self._seek_spinbox.value()
        start_frame, end_frame = self._db.get_start_end_frames(cut_frame)
        if cut_frame <= start_frame or cut_frame >= end_frame - 1:
            return

        # Insert a new frame in the database
        new_frame_index = self._db.add_frame(cut_frame)
        print(self._db)

        # Insert a new shot widget
        new_shot_widget = ShotWidget(self._video_path, self._fps, cut_frame, end_frame)
        new_shot_widget.clicked.connect(self.on_shot_widget_clicked)
        self._shot_widgets.insert(new_frame_index, new_shot_widget)

        # Update the end frame of the previous shot widget
        prev_shot_widget = self._shot_widgets[new_frame_index - 1]
        prev_shot_widget.set_end_frame(cut_frame)

        # Update the grid layout
        self.update_grid_layout()


    @log_function_name(color=PRINT_GREEN_COLOR)
    def merge_selected_shots(self):
        assert self._shot_widgets
        if not self._shot_widgets:
            return

        if self.IsSelectionEmpty():
            return

        index_min, index_max = self.get_selection_index_min_max()
        shot_widget_min = self._shot_widgets[index_min]
        shot_widget_max = self._shot_widgets[index_max]
        shot_widget_min.set_end_frame(shot_widget_max.get_end_frame())

        self.clear_shot_widget_selection()
        del self._shot_widgets[index_min + 1 : index_max + 1]

        del self._db[index_min + 1 : index_max + 1]
        print(self._db)
        
        # Update the grid layout
        self.update_grid_layout()


    @log_function_name()
    def detect_shots(self):
        assert self._video_path
        if not self._video_path:
            return
        
        cap = cv2.VideoCapture(self._video_path)
        fps = cap.get(cv2.CAP_PROP_FPS)

        self._db.clear()
        self._db.add_frame(0)
        self._media_player.setPosition(0)
        self._media_player.pause()

        prev_frame_gray = None
        prev_frame_hist = None

        # Create a progress dialog
        progress_dialog = QProgressDialog("Detecting shots...", "Cancel", 0, self._frame_count, self)
        progress_dialog.setWindowModality(Qt.WindowModal)
        progress_dialog.setWindowTitle("Shot detection")
        progress_dialog.setWindowFlags(progress_dialog.windowFlags() & ~Qt.WindowContextHelpButtonHint)
        progress_dialog.setMinimumDuration(0)
        progress_dialog.setValue(0)

        frame_count = 0
        while cap.isOpened():
            # Read next frame
            ret, frame = cap.read()
            if not ret:
                break
            
            # Convert frame to grayscale
            frame_gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
            frame_hist = cv2.calcHist([frame_gray], [0], None, [HISTOGRAM_BINS], [0, 256])

            if prev_frame_gray is not None:
                # Compare histograms
                hist_diff = abs(cv2.compareHist(prev_frame_hist, frame_hist, cv2.HISTCMP_CHISQR))
                hist_diff /= frame_gray.size

                # Calculate SSIM between frames
                #ssim_value, _ = ssim(prev_frame_gray, frame_gray, full=True)

                # Calculate absolute frame difference and count non-zero pixels
                frame_diff = cv2.absdiff(frame_gray, prev_frame_gray)
                _, frame_diff_binary = cv2.threshold(frame_diff, PIXEL_BINARY_THRESHOLD, 255, cv2.THRESH_BINARY)
                non_zero_count = np.count_nonzero(frame_diff_binary) / frame_gray.size

                # Shot boundary detection with combined histogram, SSIM, and pixel differences
                #if hist_diff > HISTOGRAM_THRESHOLD and ssim_value < SSIM_THRESHOLD and non_zero_count > PIXEL_DIFF_THRESHOLD:
                if hist_diff > HISTOGRAM_THRESHOLD and non_zero_count > PIXEL_DIFF_THRESHOLD:
                    # Significant change detected, consider it a shot boundary
                    start_frame = frame_count
                    self._db.add_frame(start_frame)
                    self._media_player.setPosition(int(self.convert_frame_to_position(start_frame)))
                    self._media_player.pause()

            prev_frame_gray = frame_gray.copy()
            prev_frame_hist = frame_hist.copy()
            frame_count += 1

            # Update the progress dialog
            progress_dialog.setValue(frame_count)
            if progress_dialog.wasCanceled():
                break

        cap.release()
        progress_dialog.close()

        print(self._db)
        if len(self._db) > 0:
            self.create_and_display_shot_widgets()


    ##
    ## SELECTION
    ##


    # @log_function_name(color=PRINT_GRAY_COLOR)
    def IsSelectionEmpty(self):
        return self._selection_index_first == -1


    # @log_function_name(color=PRINT_GRAY_COLOR)
    def clear_shot_widget_selection(self):
        if self._selection_index_first > -1:
            idx_min, idx_max = self.get_selection_index_min_max()
            for i in range(idx_min, idx_max + 1):
                self._shot_widgets[i].set_selected(False)
        self._selection_index_first = -1
        self._selection_index_last = -1
        self._merge_button.setEnabled(False)


    # @log_function_name(color=PRINT_GRAY_COLOR)
    def select_shot_widget(self, index):
        self.clear_shot_widget_selection()
        self._selection_index_first = index
        self._selection_index_last = index
        self._shot_widgets[index].set_selected(True)
        self._merge_button.setEnabled(True)


    # @log_function_name(color=PRINT_GRAY_COLOR)
    def extend_shot_widget_selection(self, index):
        # If the new index doesn't change the extented selection, do nothing
        if index == self._selection_index_last:
            return

        # If the selection is empty, select from 0 to the new index
        if self._selection_index_first == -1:
            assert self._selection_index_last == -1
            for i in range(0, index + 1):
                self._shot_widgets[i].set_selected(True)
            self._selection_index_first = 0
            self._selection_index_last = index
            self._merge_button.setEnabled(True)
            return

        # Else either extend or reduce the selection
        if self._selection_index_first <= self._selection_index_last:
            if index < self._selection_index_first:  # extend the selection to the left
                for i in range(index, self._selection_index_first):
                    self._shot_widgets[i].set_selected(True)
                for i in range(self._selection_index_first + 1, self._selection_index_last + 1):
                    self._shot_widgets[i].set_selected(False)
            elif index < self._selection_index_last:  # reduce the selection
                for i in range(index + 1, self._selection_index_last + 1):
                    self._shot_widgets[i].set_selected(False)
            else:  # extend the selection to the right
                for i in range(self._selection_index_last + 1, index + 1):
                    self._shot_widgets[i].set_selected(True)
        else:  # inverted min and max
            if index > self._selection_index_first:  # extend the selection to the right
                for i in range(self._selection_index_last, self._selection_index_first):
                    self._shot_widgets[i].set_selected(False)
                for i in range(self._selection_index_first + 1, index + 1):
                    self._shot_widgets[i].set_selected(True)
            elif index > self._selection_index_last:  # reduce the selection
                for i in range(self._selection_index_last, index):
                    self._shot_widgets[i].set_selected(False)
            else:  # extend the selection to the left
                for i in range(index, self._selection_index_last):
                    self._shot_widgets[i].set_selected(True)

        self._selection_index_last = index
        self._merge_button.setEnabled(True)


    ##
    ## UTILITIES
    ##


    #@log_function_name(color=PRINT_GRAY_COLOR)
    def get_mediaplayer_position_as_frame(self):
        position = self._media_player.position()
        return self.convert_position_to_frame(position)


    #@log_function_name(color=PRINT_GRAY_COLOR)
    def get_selection_index_min_max(self):
        if self._selection_index_first <= self._selection_index_last:
            return self._selection_index_first, self._selection_index_last
        else:
            return self._selection_index_last, self._selection_index_first


    #@log_function_name(color=PRINT_GRAY_COLOR)
    def convert_frame_to_position(self, frame):
        assert self._fps > 0
        if self._fps <= 0:
            return 0
        return frame * (1000 / self._fps)


    #@log_function_name(color=PRINT_GRAY_COLOR)
    def convert_position_to_frame(self, position):
        assert self._fps > 0
        if self._fps <= 0:
            return 0
        return position * (self._fps * 0.001)


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
