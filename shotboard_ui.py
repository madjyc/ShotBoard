from shotboard_vid import *

import ffmpeg
import subprocess
import os
import queue
import weakref
from PyQt5.QtCore import Qt, QObject, pyqtSignal, QThreadPool, QRunnable, QEvent, QTimer, QMutex, QMutexLocker, QReadWriteLock, QReadLocker, QWriteLocker, QSemaphore
from PyQt5.QtWidgets import QFrame, QVBoxLayout, QLabel, QProgressBar
from PyQt5.QtGui import QImage, QPixmap


SHOT_WIDGET_DEFFERRED_LOADING = False

SHOT_WIDGET_SELECT_COLOR = "#f9a825"  # orange
SHOT_WIDGET_IMAGE_WIDTH = 296
SHOT_WIDGET_IMAGE_HEIGHT = 167
SHOT_WIDGET_MARGIN = 4  # margin between the scrollarea frame and the widgets

SHOT_WIDGET_PROGRESSBAR_COLOR = "#3a9ad9" #"#0284eb"  # blue
SHOT_WIDGET_PROGRESSBAR_HEIGHT = 15

SHOT_WIDGET_RESCAN_COLOR = "#3ad98a"  # green

SHOT_WIDGET_WIDTH = SHOT_WIDGET_IMAGE_WIDTH + 2 * SHOT_WIDGET_MARGIN + 2
SHOT_WIDGET_HEIGHT = SHOT_WIDGET_IMAGE_HEIGHT + SHOT_WIDGET_PROGRESSBAR_HEIGHT + 2 * SHOT_WIDGET_MARGIN + 2

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


    def __init__(self, video_path, fps, frame_index):
        super().__init__()
        self.setAutoDelete(True)
        self.signals = ThumbnailLoader.Signals()

        self.video_path = video_path
        self.fps = fps
        self.frame_index = frame_index
    

    def run(self):
        """ Simulate loading an image by creating a black QPixmap """
        pixmap = QPixmap(SHOT_WIDGET_IMAGE_WIDTH, SHOT_WIDGET_IMAGE_HEIGHT)
        pixmap.fill(Qt.darkCyan)  # Default black in case of error

        start_pos = self.frame_index / self.fps  # no offset for FFmpeg

        # ffmpeg_cmd = [
        #     "ffmpeg", "-y", "-ss", str(start_pos),
        #     "-i", self.video_path, "-vframes", "1",
        #     "-vf", f"scale={SHOT_WIDGET_IMAGE_WIDTH}:{SHOT_WIDGET_IMAGE_HEIGHT}",
        #     "-f", "image2", "-vcodec", "mjpeg", "pipe:1"
        # ]

        # Construct FFmpeg command using ffmpeg-python
        with QMutexLocker(ThumbnailLoader.ffmpeg_mutex):
            ffmpeg_cmd = ffmpeg.input(self.video_path, ss=start_pos)

            if ShotWidget.detect_edges:
                ffmpeg_cmd = (
                    ffmpeg_cmd
                    .filter('format', 'gray')  # Convert to grayscale
                    .filter('sobel', scale=ShotWidget.edge_factor)  # Edge detection
                    .filter('negate')  # Invert colors
                )

            # Run FFmpeg directly through ffmpeg-python
            try:
                out, _ = (
                    ffmpeg_cmd
                    .output('pipe:', vframes=1, format='image2', vcodec='mjpeg')
                    .run(capture_stdout=True, capture_stderr=False)
                )

                if out:
                    pixmap.loadFromData(out)

            except ffmpeg.Error as e:
                print("Error: Cannot extract frame with FFmpeg.")
                print(e.stderr.decode())
                self.signals.thumbnail_failed.emit(self.frame_index)
                return

        self.signals.thumbnail_loaded.emit(self.frame_index, pixmap)


class ThumbnailManager(QObject):
    """Manages asynchronous loading of QPixmaps."""

    thumbnail_loaded = pyqtSignal(int, QPixmap)  # Signal emitted when an image is ready


    def __init__(self):
        super().__init__()
        self.thread_pool = QThreadPool.globalInstance()
        self.lock = QReadWriteLock()  # 🔹 Use Read-Write Lock
        self.queue = []  # Frame index queue
        self.priority_list = []  # High-priority frame indexes
        self.thumbnails = {}  # Loaded pixmaps
        self.running_tasks = set()  # Tracks currently loading frame indexes
        self.is_processing_queue = False

        self.video_path = None
        self.fps = None


    def set_video(self, video_path, fps):
        self.video_path = video_path
        self.fps = fps


    def clear(self):
        """Clears all stored thumbnails and resets the queue."""
        with QWriteLocker(self.lock):
            self.running_tasks.clear()
            self.thumbnails.clear()
        self.queue.clear()
        self.clear_priority_list()


    def clear_priority_list(self):
        self.priority_list.clear()
        self.safe_disconnect()  # disconnect all shot widget slots


    def safe_disconnect(self):
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
            self.start_processing_queue()


    def add_frame_indexes_to_queue(self, frame_indexes):
        """ Add frame indexes to the queue """
        unique_indexes = set(frame_indexes) - set(self.queue)  # Remove duplicates
        if unique_indexes:
            self.queue.extend(unique_indexes)
            self.start_processing_queue()


    def add_frame_index_to_priority_list(self, frame_index):
        """ Add frame index to the priority list """
        if self.has_thumbnail(frame_index):
            return
        
        if frame_index not in self.priority_list:
            self.priority_list.append(frame_index)


    def add_frame_indexes_to_priority_list(self, frame_indexes):
        """ Add frame indexes to the priority list """
        unique_indexes = set(frame_indexes) - set(self.priority_list)  # Remove duplicates
        if unique_indexes:
            self.priority_list.extend(unique_indexes)


    def request_thumbnail(self, frame_index, priority=False):
        """Adds a frame index to the queue or priority list if needed."""

        print(f"....ThumbnailManager.request_thumbnail() > shot_widget {frame_index} requesting thumbnail...")
        # Already loaded? Respond immediately
        if self.has_thumbnail(frame_index):
            print(f"....ThumbnailManager.request_thumbnail() > shot_widget {frame_index} emitting...")
            image = self.get_thumbnail(frame_index)
            print(f"......ThumbnailManager.request_thumbnail() > shot_widget {frame_index} image fetched.")
            self.thumbnail_loaded.emit(frame_index, image)
            print(f"....ThumbnailManager.request_thumbnail() > shot_widget {frame_index} emitted.")
            return

        # Else store the requested frame index and start processing the queue
        if frame_index not in self.queue:
            self.queue.append(frame_index)  # Add to normal queue

        if priority and frame_index not in self.priority_list:
            self.priority_list.append(frame_index)  # Add to priority list
        print(f"....ThumbnailManager.request_thumbnail() > shot_widget {frame_index} thumbnail requested.")

        self.start_processing_queue()


    def start_processing_queue(self):
        QTimer.singleShot(0, self.process_queue)  # 100 ms delay


    def process_queue(self):
        """Processes the priority list first, then the queue, and starts workers."""
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
                image = self.get_thumbnail(frame_index)
                self.thumbnail_loaded.emit(frame_index, image)
                continue

            with QReadLocker(self.lock):
                if frame_index in self.running_tasks:
                    continue  # Skip if this frame is already being loaded

            with QWriteLocker(self.lock):
                self.running_tasks.add(frame_index)
            loader = ThumbnailLoader(self.video_path, self.fps, frame_index)
            loader.signals.thumbnail_loaded.connect(self.on_thumbnail_loaded)
            loader.signals.thumbnail_failed.connect(self.on_loading_failed)
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
        self.start_processing_queue()


    def on_loading_failed(self, frame_index):
        """Called when ffmpeg was unable to load the requested frame."""
        with QWriteLocker(self.lock):
            self.running_tasks.discard(frame_index)
        self.start_processing_queue()


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
        return f"ThumbnailContainer({list(self.thumbnails.keys())})"


##
## SHOT WIDGET
##


class ShotWidget(QFrame):
    # Signals
    clicked = pyqtSignal(bool)  # Signal includes a boolean to indicate Shift key status

    # Shared ThumbnailManager for all instances
    thumbnail_manager = ThumbnailManager()

    # Shared data
    volume = 0
    speed = 1.0
    detect_edges = False
    edge_factor = 1.0


    def __init__(self, video_path, fps, start_frame_index, end_frame_index):
        super().__init__()
        self.videoplayer = None
        self.video_path = video_path
        self.fps = fps
        self.start_frame_index = start_frame_index  # included
        self.end_frame_index = end_frame_index  # excluded (i.e. next frame's start_frame_index)
        self.is_selected = False

        self.setFixedSize(SHOT_WIDGET_WIDTH, SHOT_WIDGET_HEIGHT)  #  256 x 148 px
        self.setFrameStyle(QFrame.Box)

        layout = QVBoxLayout()

        # Create a QLabel to display the image (screenshot) and scale it to 256 x 128 px
        self.image_label = QLabel()
        self.image_label.setMaximumSize(SHOT_WIDGET_IMAGE_WIDTH, SHOT_WIDGET_IMAGE_HEIGHT)  #  256 x 128 px
        self.image_label.setAlignment(Qt.AlignCenter)
        self.image_label.setStyleSheet("background-color: black;")
        self.image_label.installEventFilter(self)
        self.image_label.setMouseTracking(True)
        layout.addWidget(self.image_label)

        self.setMouseTracking(True)  # Enable tracking for the whole widget

        # Create a timer to hide the mouse cursor when inactive
        self.cursor_timer = QTimer(self)
        self.cursor_timer.setInterval(SHOT_WIDGET_HIDE_CURSOR_TIMEOUT)
        self.cursor_timer.timeout.connect(self.on_cursor_timer_timeout)

        # Create a progress bar to display the frame index
        self.frame_progress_bar = QProgressBar()
        self.frame_progress_bar.setMaximumHeight(SHOT_WIDGET_PROGRESSBAR_HEIGHT)
        self.frame_progress_bar.setAlignment(Qt.AlignCenter)
        self.frame_progress_bar.setStyleSheet(f"""
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
        self.frame_progress_bar.setMinimum(start_frame_index)
        self.frame_progress_bar.setMaximum(end_frame_index - 1)
        self.frame_progress_bar.setValue(start_frame_index)
        self.frame_progress_bar.setFormat("Frame %v")
        layout.addWidget(self.frame_progress_bar)

        self.setLayout(layout)
        layout.setContentsMargins(SHOT_WIDGET_MARGIN, SHOT_WIDGET_MARGIN, SHOT_WIDGET_MARGIN, SHOT_WIDGET_MARGIN)

        self.thumbnail_loaded = False
        if SHOT_WIDGET_DEFFERRED_LOADING:
            # self.request_thumbnail()
            pass
        else:
            self.initialise_thumbnail()


    def is_thumbnail_loaded(self):
        return self.thumbnail_loaded


    def get_margins(self):
        return self.layout().contentsMargins()


    def get_start_frame_index(self):
        return self.start_frame_index
    

    def get_end_frame_index(self):
        return self.end_frame_index


    def set_end_frame_index(self, end_frame_index, reset_thumbnail):
        assert end_frame_index > self.start_frame_index
        self.end_frame_index = end_frame_index
        self.frame_progress_bar.setMaximum(end_frame_index - 1)
        self.frame_progress_bar.setValue(self.start_frame_index)
        if reset_thumbnail:
            if SHOT_WIDGET_DEFFERRED_LOADING:
                self.request_thumbnail()
            else:
                self.initialise_thumbnail()


    def set_selected(self, selected):
        self.is_selected = selected
        if self.is_selected:
            self.setStyleSheet(f"background-color: {SHOT_WIDGET_SELECT_COLOR};")
        else:
            self.setStyleSheet("background-color: none;")


    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            shift_pressed = event.modifiers() & Qt.ShiftModifier
            self.clicked.emit(bool(shift_pressed))


    def eventFilter(self, source, event):
        if source == self.image_label:
            if event.type() == QEvent.Enter:
                self.on_image_label_enter()
            elif event.type() == QEvent.Leave:
                self.on_image_label_leave()
        return super().eventFilter(source, event)


    def mouseMoveEvent(self, event):
        """Show the cursor and restart the timer when the mouse moves."""
        self.setCursor(Qt.ArrowCursor)  # Ensure cursor is visible
        self.cursor_timer.start()  # Restart the countdown
        super().mouseMoveEvent(event)


    def on_cursor_timer_timeout(self):
        """Hide the cursor only if still inside the widget."""
        if self.underMouse():  # Check if the mouse is still inside
            self.setCursor(Qt.BlankCursor)


    def initialise_thumbnail(self):
        if not os.path.isfile(self.video_path):
            print(f"Error: File {self.video_path} does not exist.")
            return

        assert self.fps > 0
        start_pos = self.start_frame_index / self.fps  # no offset for FFmpeg

        # Use FFmpeg to extract the frame
        ffmpeg_cmd = ffmpeg.input(self.video_path, ss=start_pos)
        if ShotWidget.detect_edges:
            ffmpeg_cmd = (
                ffmpeg_cmd
                .filter('format', 'gray')  # Convert to grayscale
                .filter('sobel', scale=ShotWidget.edge_factor)  # Edge detection
                .filter('negate')  # Invert colors
            )
        ffmpeg_cmd = ffmpeg_cmd.output('pipe:', vframes=1, format='image2', vcodec='mjpeg').compile()

        process = subprocess.Popen(ffmpeg_cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
        out, err = process.communicate()
        if process.returncode != 0:
            print("Error: Cannot extract frame with FFmpeg.")
            print(err.decode())
            return

        # Load the extracted frame using QImage
        image = QImage.fromData(out)
        if image.isNull():
            print("Error: Cannot load extracted frame.")
            return

        # Update the image label
        self.update_frame_from_image(self.start_frame_index, image)


    def on_image_label_enter(self):
        self.cursor_timer.start()  # Start tracking inactivity
        self.videoplayer = VideoPlayer(self.video_path, self.fps, self.start_frame_index, self.end_frame_index, ShotWidget.volume, ShotWidget.speed, ShotWidget.detect_edges, ShotWidget.edge_factor)
        if self.videoplayer:
            self.videoplayer.frame_signal.connect(self.on_frame_loaded)
            self.videoplayer.start()  # Start the video rendering thread


    def on_image_label_leave(self):
        self.cursor_timer.stop()  # Stop hiding cursor
        self.setCursor(Qt.ArrowCursor)  # Ensure the cursor is visible
        if self.videoplayer:
            self.videoplayer.stop()
            self.videoplayer = None


    def on_frame_loaded(self):
        assert self.videoplayer
        if not self.videoplayer._frame_queue.empty():
            try:
                frame_index, image = self.videoplayer._frame_queue.get()  # Thread-safe
                #print(f"Start updating frame {frame_index}")
                self.update_frame_from_image(frame_index, image)
            except queue.Empty:
                print("Warning: Frame queue is empty. Skipping frame.")
                return
            except Exception as e:
                print(f"Error retrieving frame from queue: {e}")
                return


    def update_frame_from_image(self, frame_index, image):
        self.update_frame(frame_index, QPixmap.fromImage(image))


    def update_frame(self, frame_index, pixmap):
        scaled_pixmap = pixmap.scaled(
            self.image_label.maximumWidth(),
            self.image_label.maximumHeight(),
            Qt.KeepAspectRatio,
            Qt.SmoothTransformation
        )
        self.image_label.setPixmap(scaled_pixmap)
        self.frame_progress_bar.setValue(frame_index)
        self.thumbnail_loaded = True


    ##
    ## DEFFERED THUMBNAIL LOADING
    ##


    def request_thumbnail(self, force=False):
        """Request a thumbnail from the shared ThumbnailManager."""
        if force:
            self.thumbnail_loaded = False
        
        if not self.thumbnail_loaded:
            self.safe_disconnect()
            ShotWidget.thumbnail_manager.thumbnail_loaded.connect(self.on_thumbnail_loaded, Qt.QueuedConnection)
            print(f"..ShotWidget.request_thumbnail() > shot_widget {self.start_frame_index} requesting thumbnail...")
            ShotWidget.thumbnail_manager.request_thumbnail(self.start_frame_index, priority=True)
            print(f"..ShotWidget.request_thumbnail() > shot_widget {self.start_frame_index} thumbnail requested.")


    # Callback function called directly by ThumbnailManager when a thumbnail is loaded
    def on_thumbnail_loaded(self, frame_index, pixmap):
        """Set the pixmap when it's loaded."""
        assert SHOT_WIDGET_DEFFERRED_LOADING
        if frame_index != self.start_frame_index:
            return

        self.safe_disconnect()
        self.update_frame(0, pixmap)
        self.thumbnail_loaded = True
        print(f"Thumbnail {frame_index} loaded")


    def safe_disconnect(self):
        assert SHOT_WIDGET_DEFFERRED_LOADING
        try:
            ShotWidget.thumbnail_manager.thumbnail_loaded.disconnect(self.on_thumbnail_loaded)
        except TypeError:
            pass  # Already disconnected


    def closeEvent(self, event):
        self.on_image_label_leave()
        if SHOT_WIDGET_DEFFERRED_LOADING:
            self.safe_disconnect()
        event.accept()


    def __del__(self):
        """ Ensure the VideoPlayer is stopped when the object is deleted """
        if self.videoplayer:
            self.videoplayer.stop()


if __name__ == "__main__":
    print(f"\033[91mTHIS MODULE FILE IS NOT MEANT TO BE RUN!\033[0m")
