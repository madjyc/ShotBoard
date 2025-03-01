from shotboard_vid import *

import subprocess
import queue
import bisect
import datetime
from PyQt5.QtCore import Qt, QObject, pyqtSignal, QThreadPool, QRunnable, QEvent, QTimer, QMutex, QMutexLocker, QReadWriteLock, QReadLocker, QWriteLocker, QRect
from PyQt5.QtWidgets import QFrame, QVBoxLayout, QLabel, QProgressBar
from PyQt5.QtGui import QImage, QPixmap


# Image dimension for storage
STORED_IMAGE_SIZE = (1024, 576) # h = w * 0.5625  # ratio = 16/9

SHOT_WIDGET_SELECT_COLOR = "#f9a825"  # orange
SHOT_WIDGET_MARGIN = 4  # also margin between the scrollarea frame and the widgets
GRID_LAYOUT_SPACING = 6

SHOT_IMAGE_SIZES = {}
SHOT_IMAGE_SIZES_MIN = 4
SHOT_IMAGE_SIZES_MAX = 10
DEFAULT_SHOT_IMAGE_SIZE = 7
assert SHOT_IMAGE_SIZES_MAX >= SHOT_IMAGE_SIZES_MIN
for i in range(SHOT_IMAGE_SIZES_MIN, SHOT_IMAGE_SIZES_MAX + 1):
    TOTAL_TARGET_WIDTH = 1866
    img_width = int((TOTAL_TARGET_WIDTH + GRID_LAYOUT_SPACING) / i - (2 * SHOT_WIDGET_MARGIN + 2) - GRID_LAYOUT_SPACING)
    img_height = round(img_width * 0.5625)  # ratio = 16/9
    SHOT_IMAGE_SIZES[i] = (img_width, img_height)

SHOT_WIDGET_PROGRESSBAR_COLOR = "#3a9ad9" #"#0284eb"  # blue
SHOT_WIDGET_PROGRESSBAR_HEIGHT = 15

SHOT_WIDGET_RESCAN_COLOR = "#3ad98a"  # green

SHOT_WIDGET_HIDE_CURSOR_TIMEOUT = 200  # in ms

# frame_thickness = self.scroll_area.frameWidth()
# scrollarea_height = self.scroll_area.viewport().height()
# visible_area_top = self.scroll_area.verticalScrollBar().value()
# visible_area_bottom = visible_area_top + scrollarea_height
# vertical_spacing = self.grid_layout.verticalSpacing()
# widget_height = clip_widget.height()
# row, _, _, _ = self.grid_layout.getItemPosition(index)
# clip_widget_top = SHOT_WIDGET_MARGIN + row * (widget_height + vertical_spacing)
# clip_widget_bottom = clip_widget_top + widget_height


##
## ASYNC IMAGE LOADER
##


class ThumbnailLoader(QRunnable):
    ffmpeg_mutex = QMutex()

    """ Asynchronous task to generate and return a QPixmap """
    class Signals(QObject):
        thumbnail_loaded  = pyqtSignal(int, QPixmap)
        thumbnail_failed  = pyqtSignal(int)


    def __init__(self, video_info, frame_index):
        super().__init__()
        self.setAutoDelete(True)
        self._running = True
        self._signals = ThumbnailLoader.Signals()
        self._signals.destroyed.connect(self._on_signals_destroyed)

        self._video_info = video_info
        self._frame_index = frame_index
    

    def run(self):
        """ Simulate loading an image by creating a black QPixmap """
        START_POS = max(0, (self._frame_index - FFMPEG_FRAME_SEEK_OFFSET) / self._video_info.fps)  # frame position in seconds

        with QMutexLocker(ThumbnailLoader.ffmpeg_mutex):
            # Run FFmpeg without showing a console window
            ffmpeg_cmd = [
                "ffmpeg",
                "-loglevel", "quiet",  # Suppress all FFmpeg logging
                "-ss", str(START_POS),  # Fast seek FIRST
                "-i", self._video_info.video_path,  # Input file AFTER
                "-vframes", "1",  # Number of frames to process
                "-f", "image2",  # Output format
                "-vcodec", "mjpeg",  # Video codec
                "-nostdin",  # Disable interaction on standard input
                "-"  # Output to pipe
            ]

            process = subprocess.Popen(
                ffmpeg_cmd,
                stdout=subprocess.PIPE,  # Capture stdout
                stderr=subprocess.DEVNULL,  # Discard stderr
                **FFMPEG_NOWINDOW_KWARGS
            )

            if self._running:
                out, err = process.communicate()
                if process.returncode != 0:
                    print("Error: Cannot extract frame with FFmpeg.")
                    print(err.decode())
                    self._signals.thumbnail_failed.emit(self._frame_index)
                    return

            if self._running:
                pixmap = QPixmap(STORED_IMAGE_SIZE[0], STORED_IMAGE_SIZE[1])
                if pixmap.loadFromData(out):
                    pixmap = pixmap.scaled(
                        STORED_IMAGE_SIZE[0], 
                        STORED_IMAGE_SIZE[1], 
                        Qt.KeepAspectRatio, 
                        Qt.SmoothTransformation  # For some reason, Qt.SmoothTransformation works fine here, but not downstream
                    )
                else:
                    pixmap.fill(Qt.darkCyan)  # Default black in case of error
                    print("Failed to load image data.")

            if self._running:
                self._signals.thumbnail_loaded.emit(self._frame_index, pixmap)


    def _on_signals_destroyed(self):
        """ Mark the task as inactive when its signals object is deleted. """
        self._running = False


    def __del__(self):
        """ Mark the task as inactive when it gets garbage collected. """
        self._running = False


##
## IMAGE MANAGER
##


class ThumbnailManager(QObject):
    """Manages asynchronous loading of QPixmaps."""

    thumbnail_loaded = pyqtSignal(int, QPixmap)  # Signal emitted when an image is ready


    def __init__(self):
        super().__init__()
        self.thread_pool = QThreadPool.globalInstance()
        # self.thread_pool.setMaxThreadCount(4)  # DEBUG
        self.lock = QReadWriteLock()
        self.queue = []  # Frame index queue
        self.priority_list = []  # High-priority frame indexes
        self.thumbnails = {}  # Loaded pixmaps
        self.running_tasks = set()  # Tracks currently loading frame indexes
        self.is_processing_queue = False

        self._video_info = None


    def set_video_info(self, video_info):
        self._video_info = video_info


    def clear(self):
        """Clears all stored thumbnails and resets the queue."""
        self.safe_disconnect_from_loaders()
        self.safe_disconnect_thumbnail_loaded_signal()  # disconnect all shot widget slots
        with QWriteLocker(self.lock):
            self.running_tasks.clear()
            self.thumbnails.clear()
        self.queue.clear()
        self.clear_priority_list()


    def clear_priority_list(self):
        self.priority_list.clear()


    def safe_disconnect_from_loaders(self):
        """Disconnect all shot widget slots"""
        try:
            self.disconnect(self.on_thumbnail_loaded)
            self.disconnect(self.on_loading_failed)
        except TypeError:
            pass  # Already disconnected


    def safe_disconnect_thumbnail_loaded_signal(self):
        """Disconnect all shot widget slots"""
        try:
            self.thumbnail_loaded.disconnect()
        except TypeError:
            pass  # Already disconnected


    def add_frame_index_to_queue(self, frame_index):
        """ Add frame index to the queue """
        if self.has_thumbnail(frame_index):
            return
        
        if frame_index not in self.queue:
            self.queue.append(frame_index)
            self.process_queue()


    def add_frame_indexes_to_queue(self, frame_indexes):
        """ Add frame indexes to the queue """
        unique_indexes = sorted(set(frame_indexes) - set(self.queue))  # Remove duplicates
        if unique_indexes:
            self.queue.extend(unique_indexes)
            self.process_queue()


    def add_frame_index_to_priority_list(self, frame_index):
        """ Add frame index to the priority list """
        if self.has_thumbnail(frame_index):
            return
        
        if frame_index not in self.priority_list:
            self.priority_list.append(frame_index)


    def add_frame_indexes_to_priority_list(self, frame_indexes):
        """ Add frame indexes to the priority list """
        unique_indexes = sorted(set(frame_indexes) - set(self.priority_list))  # Remove duplicates
        if unique_indexes:
            self.priority_list.extend(unique_indexes)


    def request_thumbnail(self, frame_index, priority):
        """Adds a frame index to the queue if needed."""
        # Already loaded? Respond immediately
        if self.has_thumbnail(frame_index):
            self.thumbnail_loaded.emit(frame_index, self.get_thumbnail(frame_index))
            return

        # Else store the requested frame index and start processing the queue
        if frame_index not in self.queue:
            self.queue.append(frame_index)

        if priority and frame_index not in self.priority_list:
            self.priority_list.append(frame_index)  # Add to priority list

        self.process_queue()


    def process_queue(self):
        """Processes the priority list first, then the queue, and starts worker threads."""
        if self.is_processing_queue:
            return
        self.is_processing_queue = True

        while self.thread_pool.activeThreadCount() < self.thread_pool.maxThreadCount():
            if self.priority_list:
                frame_index = self.priority_list.pop(0)  # Process priority first
                self.queue.remove(frame_index)  # Remove from queue as well
            elif self.queue:
                frame_index = self.queue.pop(0)  # Process normal queue
            else:
                break  # Nothing left to process

            if self.has_thumbnail(frame_index):
                self.thumbnail_loaded.emit(frame_index, self.get_thumbnail(frame_index))
                continue

            with QReadLocker(self.lock):
                if frame_index in self.running_tasks:
                    continue  # Skip if this frame is already being loaded

            with QWriteLocker(self.lock):
                self.running_tasks.add(frame_index)
            loader = ThumbnailLoader(self._video_info, frame_index)
            loader._signals.thumbnail_loaded.connect(self.on_thumbnail_loaded)
            loader._signals.thumbnail_failed.connect(self.on_loading_failed)
            # print(f"activeThreadCount={self.thread_pool.activeThreadCount()}/{self.thread_pool.maxThreadCount() - 1}")
            self.thread_pool.start(loader)
            # assert len(self.running_tasks) == self.thread_pool.maxThreadCount() - self.thread_pool.activeThreadCount()
        self.is_processing_queue = False


    def on_thumbnail_loaded(self, frame_index, pixmap):
        """Store the loaded pixmap and emit a signal."""
        with QWriteLocker(self.lock):
            self.thumbnails[frame_index] = pixmap
            self.running_tasks.discard(frame_index)
        self.thumbnail_loaded.emit(frame_index, pixmap)
        self.process_queue()


    def on_loading_failed(self, frame_index):
        """Called when ffmpeg was unable to load the requested frame."""
        with QWriteLocker(self.lock):
            self.running_tasks.discard(frame_index)
        self.process_queue()


    def has_thumbnail(self, frame_index):
        """ Check if a thumbnail exists """
        with QReadLocker(self.lock):
            return frame_index in self.thumbnails


    def get_thumbnail(self, frame_index):
        """Returns the loaded QPixmap or None if not available."""
        with QReadLocker(self.lock):
            return self.thumbnails.get(frame_index, None)


    def __len__(self):
        """Return the number of stored thumbnails."""
        with QReadLocker(self.lock):
            return len(self.thumbnails)
    

    def __contains__(self, frame_index):
        """Allows 'in' operator to check existence."""
        return self.has_thumbnail(frame_index)


    def __iter__(self):
        """Return an iterator over frame indices."""
        with QReadLocker(self.lock):
            return iter(self.thumbnails.copy())  # Copy to avoid modification during iteration


    def __str__(self):
        """Return a string representation of the stored thumbnails."""
        with QReadLocker(self.lock):
            return f"ThumbnailContainer({list(self.thumbnails.keys())})"


##
## SHOT WIDGET
##


class ShotWidget(QFrame):
    # Signals
    hovered = pyqtSignal(bool)  # Signal includes a boolean to indicate if the mouse cursor is entering (True) or leaving (False) the widget
    clicked = pyqtSignal(bool)  # Signal includes a boolean to indicate Shift key status

    # Shared ThumbnailManager for all instances
    thumbnail_manager = ThumbnailManager()

    # Shared data
    volume = 0
    speed = 1.0
    detect_edges = False
    edge_factor = 1.0


    @staticmethod
    def evaluate_image_size(num_img_per_row):
        if num_img_per_row in SHOT_IMAGE_SIZES:
            image_size = SHOT_IMAGE_SIZES[num_img_per_row]
            return image_size
        else:
            raise KeyError(f"Error - Unkown number of images per row {num_img_per_row}")


    @staticmethod
    def evaluate_widget_size(num_img_per_row):
        if num_img_per_row in SHOT_IMAGE_SIZES:
            image_size = SHOT_IMAGE_SIZES[num_img_per_row]
            widget_size = (image_size[0] + (2 * SHOT_WIDGET_MARGIN) + 2, image_size[1] + SHOT_WIDGET_PROGRESSBAR_HEIGHT + (3 * SHOT_WIDGET_MARGIN) + 2)
            return widget_size
        else:
            raise KeyError(f"Error - Unkown number of images per row {num_img_per_row}")


    def __init__(self, shot_number, video_info, start_frame_index, end_frame_index, num_img_per_row):
        super().__init__()
        self._videoplayer = None
        self._shot_number = shot_number
        self._video_info = video_info
        self._start_frame_index = start_frame_index  # included
        self._end_frame_index = end_frame_index  # excluded (i.e. next frame's start_frame_index)
        self._is_selected = False

        # Calculate shot timestamp
        self._duration_msf = self.format_duration(self._start_frame_index, self._end_frame_index, self._video_info.fps)

        layout = QVBoxLayout()
        layout.setContentsMargins(SHOT_WIDGET_MARGIN, SHOT_WIDGET_MARGIN, SHOT_WIDGET_MARGIN, SHOT_WIDGET_MARGIN)
        layout.setSpacing(SHOT_WIDGET_MARGIN)

        # Create a QLabel to display the thumbnail
        self._image_label = QLabel()
        self._image_label.setAlignment(Qt.AlignCenter)
        self._image_label.setStyleSheet("background-color: black;")
        self._image_label.installEventFilter(self)
        self._image_label.setMouseTracking(True)
        layout.addWidget(self._image_label)

        self.setMouseTracking(True)  # Enable tracking for the whole widget

        # Create a timer to hide the mouse cursor when inactive
        self._cursor_timer = QTimer(self)
        self._cursor_timer.setInterval(SHOT_WIDGET_HIDE_CURSOR_TIMEOUT)
        self._cursor_timer.timeout.connect(self.on_cursor_timer_timeout)

        # Create a progress bar to display the frame index
        self._frame_progress_bar = QProgressBar()
        self._frame_progress_bar.setMaximumHeight(SHOT_WIDGET_PROGRESSBAR_HEIGHT)
        self._frame_progress_bar.setAlignment(Qt.AlignCenter)
        self._frame_progress_bar.setStyleSheet(f"""
            QProgressBar {{
                background-color: white;
                text-align: center;
                color: black;  /* Text color */
                font-weight: bold;
            }}
            QProgressBar::chunk {{
                background-color: {SHOT_WIDGET_PROGRESSBAR_COLOR};  /* Progress bar color */
            }}
        """)
        self._frame_progress_bar.setMinimum(start_frame_index)
        self._frame_progress_bar.setMaximum(end_frame_index - 1)
        self.update_progress_bar(start_frame_index)
        layout.addWidget(self._frame_progress_bar)

        self.setLayout(layout)

        self.setFrameStyle(QFrame.Box)
        self.resize(num_img_per_row)
        self._thumbnail_loaded = False


    def format_duration(self, start_frame_index, end_frame_index, fps):
        total_frames = end_frame_index - start_frame_index
        total_seconds = total_frames // fps
        remaining_frames = total_frames % fps

        minutes = total_seconds // 60
        seconds = total_seconds % 60

        return f"{int(minutes):02}:{int(seconds):02}+{int(remaining_frames):02}"


    def set_shot_number(self, number):
        self._shot_number = number


    def resize(self, num_img_per_row):
        image_size = ShotWidget.evaluate_image_size(num_img_per_row)
        self._image_label.setMaximumSize(image_size[0], image_size[1])

        widget_size = ShotWidget.evaluate_widget_size(num_img_per_row)
        self.setFixedSize(widget_size[0], widget_size[1])

        self.initialise_thumbnail(False)


    def is_thumbnail_loaded(self):
        return self._thumbnail_loaded


    def get_margins(self):
        return self.layout().contentsMargins()


    def get_start_frame_index(self):
        return self._start_frame_index
    

    def get_end_frame_index(self):
        return self._end_frame_index


    def set_end_frame_index(self, end_frame_index, reset_thumbnail):
        assert end_frame_index > self._start_frame_index
        self._end_frame_index = end_frame_index
        self._frame_progress_bar.setMaximum(end_frame_index - 1)
        self._duration_msf = self.format_duration(self._start_frame_index, self._end_frame_index, self._video_info.fps)
        self.update_progress_bar(self._start_frame_index)
        if reset_thumbnail:
            self.initialise_thumbnail(True)


    def set_selected(self, selected):
        self._is_selected = selected
        if self._is_selected:
            self.setStyleSheet(f"background-color: {SHOT_WIDGET_SELECT_COLOR};")
        else:
            self.setStyleSheet("background-color: none;")


    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            shift_pressed = event.modifiers() & Qt.ShiftModifier
            self.clicked.emit(bool(shift_pressed))


    def eventFilter(self, source, event):
        if source == self._image_label:
            if event.type() == QEvent.Enter:
                self.on_image_label_enter()
            elif event.type() == QEvent.Leave:
                self.on_image_label_leave()
        return super().eventFilter(source, event)


    def mouseMoveEvent(self, event):
        """Show the cursor and restart the timer when the mouse moves."""
        self.setCursor(Qt.ArrowCursor)  # Ensure cursor is visible
        self._cursor_timer.start()  # Restart the countdown
        super().mouseMoveEvent(event)


    def on_cursor_timer_timeout(self):
        """Hide the cursor only if still inside the widget."""
        if self.underMouse():  # Check if the mouse is still inside
            self.setCursor(Qt.BlankCursor)


    def on_image_label_enter(self):
        self._cursor_timer.start()  # Start tracking inactivity
        self._videoplayer = VideoPlayer(self._video_info, self._start_frame_index, self._end_frame_index, ShotWidget.volume, ShotWidget.speed, ShotWidget.detect_edges, ShotWidget.edge_factor)
        if self._videoplayer:
            self.safe_disconnect_from_thumbnail_manager()  # In case it was waiting for a thumbnail
            self._videoplayer.frame_signal.connect(self.on_frame_loaded)
            self._videoplayer.start()  # Start the video rendering thread
        self.hovered.emit(True)


    def on_image_label_leave(self):
        self._cursor_timer.stop()  # Stop hiding cursor
        self.setCursor(Qt.ArrowCursor)  # Ensure the cursor is visible
        if self._videoplayer:
            self._videoplayer.stop()  # Force disconnection
            self._videoplayer = None
        self.hovered.emit(False)


    def initialise_thumbnail(self, priority=False):
        if ShotWidget.thumbnail_manager.has_thumbnail(self._start_frame_index):
            pixmap = ShotWidget.thumbnail_manager.get_thumbnail(self._start_frame_index)
            self.update_frame(self._start_frame_index, pixmap)
        else:
            self._thumbnail_loaded = False
            self.request_thumbnail(priority)


    def request_thumbnail(self, priority):
        """Request a thumbnail from the shared ThumbnailManager."""
        if not self._thumbnail_loaded:
            self.safe_disconnect_from_thumbnail_manager()
            ShotWidget.thumbnail_manager.thumbnail_loaded.connect(self.on_thumbnail_loaded, Qt.QueuedConnection)
            ShotWidget.thumbnail_manager.request_thumbnail(self._start_frame_index, priority)


    # Callback function called by ThumbnailManager when a thumbnail is loaded
    def on_thumbnail_loaded(self, frame_index, pixmap):
        """Set the thumbnail loaded by ThumbnailManager."""
        if frame_index != self._start_frame_index:
            return

        self.safe_disconnect_from_thumbnail_manager()
        self.update_frame(self._start_frame_index, pixmap)


    # Callback function called by VideoPlayer when a frame is ready to be displayed
    def on_frame_loaded(self):
        """Display the frame loaded by VideoPlayer."""
        assert self._videoplayer
        if not self._videoplayer._frame_queue.empty():
            try:
                frame_index, image = self._videoplayer._frame_queue.get()  # Thread-safe
                self.update_frame(frame_index, QPixmap.fromImage(image))
            except queue.Empty:
                print("Warning: Frame queue is empty. Skipping frame.")
                return
            except Exception as e:
                print(f"Error retrieving frame from queue: {e}")
                return


    def safe_disconnect_from_thumbnail_manager(self):
        try:
            ShotWidget.thumbnail_manager.thumbnail_loaded.disconnect(self.on_thumbnail_loaded)
        except TypeError:
            pass  # Already disconnected


    def update_frame(self, frame_index, pixmap):
        scaled_pixmap = pixmap.scaled(self._image_label.maximumWidth(), self._image_label.maximumHeight(), Qt.KeepAspectRatio, Qt.FastTransformation)  # /!\ Qt.SmoothTransformation stalls when ThreadPoll is running
        self._image_label.setPixmap(scaled_pixmap)
        self._image_label.update()  # IS THIS REALLY NECESSARY?
        self._thumbnail_loaded = True
        self.update_progress_bar(frame_index)


    def update_progress_bar(self, frame_index):
        self._frame_progress_bar.setValue(frame_index)
        self._frame_progress_bar.setFormat(f"🎥 {self._shot_number} ({self._duration_msf})  🎞 %v")


    def closeEvent(self, event):
        self.on_image_label_leave()
        self.safe_disconnect_from_thumbnail_manager()
        event.accept()


    def __del__(self):
        """ Ensure the VideoPlayer is stopped when the object is deleted """
        if self._videoplayer:
            self._videoplayer.stop()


##
## SHOT WIDGET MANAGER
##


# /!\ UNUSED
class ShotWidgetManager:
    """
    A class that manages a dictionary of ShotWidget instances, referenced by start_frame_index.
    """
    # Signals
    hovered = pyqtSignal(ShotWidget, bool)  # Signal includes the shot widget and a boolean to indicate if the mouse cursor is entering (True) or leaving (False) the widget
    clicked = pyqtSignal(ShotWidget, bool)  # Signal includes the shot widget and a boolean to indicate Shift key status


    def __init__(self):
        self._shot_widgets = {}
        self._start_frame_indexes = []  # Synchronized list to store keys in sorted order
        self._video_info = None

        ShotWidget.thumbnail_manager.thumbnail_loaded.connect(self.on_thumbnail_loaded, Qt.QueuedConnection)


    def set_video_info(self, video_info):
        self._video_info = video_info
        

    def __len__(self):
        """
        Returns the number of ShotWidget instances in the manager.
        Example: count = len(manager)
        """
        return len(self._shot_widgets)


    def __contains__(self, start_frame_index):
        """
        Allows checking if a start_frame_index exists in the manager using the 'in' operator.
        Example: if start_frame_index in manager:
        """
        return start_frame_index in self._shot_widgets


    def index(self, shot_widget):
        """
        Returns the index of the given ShotWidget.
        Example: start_frame_index = manager.index(shot_widget)
        """
        if not isinstance(shot_widget, ShotWidget):
            raise TypeError("The argument must be an instance of ShotWidget")

        return self._start_frame_indexes.index(shot_widget._start_frame_index)


    def index_by_start_frame_index(self, start_frame_index):
        """
        Returns the index of the given start frame index.
        Example: index = manager.index_by_frame_index(start_frame_index)
        """
        return self._start_frame_indexes.index(start_frame_index)


    def __getitem__(self, index):
        """
        Allows accessing ShotWidget instances using the [] operator.
        Returns the ShotWidget corresponding to the start frame index at the given position in start_frame_indexes.
        Example: shot_widget = manager.get_by_index(shot_widget_index)
        """
        if not (0 <= index < len(self._start_frame_indexes)):
            raise IndexError("Index out of range")

        # Get the start frame index from the list
        start_frame_index = self._start_frame_indexes[index]

        # Return the corresponding ShotWidget
        return self._shot_widgets[start_frame_index]


    def get_by_start_frame_index(self, start_frame_index, default=None):
        """
        Returns the ShotWidget for the given start_frame_index, or a default value if not found.
        Example: shot_widget = manager.get_by_start_frame_index(start_frame_index)
        """
        return self._shot_widgets.get(start_frame_index, default)


    def pop(self, index, default=None):
        """
        Removes and returns the ShotWidget at the given index in start_frame_indexes.
        If the index is out of range, returns the default value (if provided) or raises an IndexError.
        Example: widget = manager.pop(3)  # Removes and returns the ShotWidget at position 3
        """
        if not (0 <= index < len(self._start_frame_indexes)):
            if default is not None:
                return default
            raise IndexError("Index out of range")

        # Get the corresponding start_frame_index from the sorted list
        start_frame_index = self._start_frame_indexes.pop(index)

        # Remove and return the ShotWidget from the dictionary
        return self._shot_widgets.pop(start_frame_index, default)


    def pop_by_frame_index(self, start_frame_index, default=None):
        """
        Removes and returns the ShotWidget for the given start_frame_index, or a default value if not found.
        Example: shot_widget = manager.pop(start_frame_index)
        """
        shot_widget = self._shot_widgets.pop(start_frame_index, default)

        # Remove the key from the sorted keys list
        if start_frame_index in self._start_frame_indexes:
            self._start_frame_indexes.remove(start_frame_index)

        return shot_widget


    def __setitem__(self, index, shot_widget):
        """
        Sets the ShotWidget at the given index in start_frame_indexes.
        Example: manager[3] = shot_widget  # Updates the ShotWidget at position 3
        """
        if not (0 <= index < len(self._start_frame_indexes)):
            raise IndexError("Index out of range")

        # Get the corresponding start_frame_index from the sorted list
        start_frame_index = self._start_frame_indexes[index]

        # Update the dictionary entry
        self._shot_widgets[start_frame_index] = shot_widget


    def insert_by_start_frame_index(self, start_frame_index, shot_widget):
        """
        Inserts or updates a ShotWidget in the dictionary and ensures start_frame_indexes remains sorted.
        If start_frame_index already exists, updates the dictionary and returns its position.
        Otherwise, inserts it while maintaining sorted order and returns the new position.
        """
        # Update existing entry if possible
        if start_frame_index in self._shot_widgets:
            self._shot_widgets[start_frame_index] = shot_widget
            return self._start_frame_indexes.index(start_frame_index)  # Return existing position

        # Insert new entry
        self._shot_widgets[start_frame_index] = shot_widget
        position = bisect.bisect_left(self._start_frame_indexes, start_frame_index)
        self._start_frame_indexes.insert(position, start_frame_index)

        return position


    def __delitem__(self, index):
        """
        Deletes the ShotWidget at the given index in start_frame_indexes.
        Example: del manager[3]  # Deletes the ShotWidget at position 3
        """
        if not (0 <= index < len(self._start_frame_indexes)):
            raise IndexError("Index out of range")

        # Get the corresponding start_frame_index from the sorted list
        start_frame_index = self._start_frame_indexes[index]

        # Remove the ShotWidget from the dictionary
        del self._shot_widgets[start_frame_index]
        # shot_widget.hovered.disconnect(self.on_shot_widget_hovered)
        # shot_widget.clicked.disconnect(self.on_shot_widget_clicked)

        # Remove the start_frame_index from the list
        del self._start_frame_indexes[index]

        # Keep chain consistent
        self._bridge_previous_shot_widget(index)


    def del_by_start_frame_index(self, start_frame_index):
        """
        Allows deleting ShotWidget instances using the del operator.
        Example: manager.delete_by_start_frame_index(start_frame_index)
        """
        index = self.index_by_start_frame_index(start_frame_index)
        del self._shot_widgets[start_frame_index]
        # shot_widget.hovered.disconnect(self.on_shot_widget_hovered)
        # shot_widget.clicked.disconnect(self.on_shot_widget_clicked)

        # Remove the key from the sorted keys list
        if start_frame_index in self._start_frame_indexes:
            self._start_frame_indexes.remove(start_frame_index)

        # Keep chain consistent
        self._bridge_previous_shot_widget(index)


    def create_shot_widget(self, start_frame_index, end_frame_index, num_img_per_row):
        shot_widget = ShotWidget(-1, self._video_info, start_frame_index, end_frame_index, num_img_per_row)
        shot_widget.hovered.connect(self.on_shot_widget_hovered)
        shot_widget.clicked.connect(self.on_shot_widget_clicked)
        index = self.insert_by_start_frame_index(start_frame_index, shot_widget)
        shot_widget.set_shot_number(index)

        # Keep chain consistent
        self._bridge_previous_shot_widget(index)

        return shot_widget


    # Keep chain consistent
    def _bridge_previous_shot_widget(self, index):
        if index > 0:
            prev_shot_widget = self._shot_widgets[index - 1]
            if index < len(self):
                next_shot_widget = self._shot_widgets[index]  # Same index as deleted widget
                prev_shot_widget.set_end_frame_index(next_shot_widget.get_start_frame_index(), False)
            else:
                prev_shot_widget.set_end_frame_index(self._video_info.frame_count, False)


    def on_shot_widget_hovered(self, entering):
        shot_widget = self.sender()
        self.hovered.emit(shot_widget, entering)


    def on_shot_widget_clicked(self, shift_pressed):
        shot_widget = self.sender()
        self.clicked.emit(shot_widget, shift_pressed)


    def __iter__(self):
        """
        Allows iterating over the start_frame_index keys in the manager.
        Example: for start_frame_index in manager:
        """
        return iter(self._start_frame_indexes)


    def sorted_keys(self):
        """
        Returns a list of all start_frame_index keys in the manager.
        Example: keys = manager.keys()
        """
        return self._start_frame_indexes


    def sorted_values(self):
        """
        Returns a list of all ShotWidget instances in the manager.
        Example: shot_widgets = manager.values()
        """
        return [self._shot_widgets[key] for key in self._start_frame_indexes]


    def sorted_items(self):
        """
        Returns a list of (start_frame_index, ShotWidget) pairs in the manager.
        Example: items = manager.items()
        """
        return [(key, self._shot_widgets[key]) for key in self._start_frame_indexes]


    def clear(self):
        """
        Removes all ShotWidget instances from the manager.
        Example: manager.clear()
        """
        self._shot_widgets.clear()
        self._start_frame_indexes.clear()


    def update(self, other):
        """
        Updates the manager with items from another dictionary or iterable of (start_frame_index, ShotWidget) pairs.
        Example: manager.update({start_frame_index: shot_widget})
        """
        for start_frame_index, shot_widget in other.items():
            if not isinstance(shot_widget, ShotWidget):
                raise TypeError("Value must be an instance of ShotWidget")
            self.insert_by_start_frame_index[start_frame_index] = shot_widget  # Ensure sorted_keys is updated


    def __repr__(self):
        """
        Returns a string representation of the manager.
        Example: print(manager)
        """
        return f"ShotWidgetManager({self._shot_widgets})"


    def request_thumbnail(self, index, priority):
        ShotWidget.thumbnail_manager.request_thumbnail(self[index], priority)


    def request_thumbnail_by_start_frame_index(self, start_frame_index, priority):
        ShotWidget.thumbnail_manager.request_thumbnail(start_frame_index, priority)


    # Slot called back by ThumbnailManager when a thumbnail has finished loading
    def on_thumbnail_loaded(self, frame_index, pixmap):
        """Set the thumbnail loaded by ThumbnailManager."""
        shot_widget = self.get_by_start_frame_index(frame_index)
        if shot_widget:
            shot_widget.update_frame(frame_index, pixmap)


    def safe_disconnect_from_thumbnail_manager(self):
        try:
            ShotWidget.thumbnail_manager.thumbnail_loaded.disconnect(self.on_thumbnail_loaded)
        except TypeError:
            pass  # Already disconnected


    def __del__(self):
        """
        Things to do when the object is about to be deleted
        """
        self.safe_disconnect_from_thumbnail_manager()


if __name__ == "__main__":
    print(f"\033[91mTHIS MODULE FILE IS NOT MEANT TO BE RUN!\033[0m")
