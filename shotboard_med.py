from shotboard_vid import *

import ffmpeg
import subprocess
import os
import queue
import weakref
from enum import IntEnum, auto
from PyQt5.QtCore import Qt, QObject, pyqtSignal, QThreadPool, QRunnable, QEvent, QTimer, QMutex, QMutexLocker, QReadWriteLock, QReadLocker, QWriteLocker, QSemaphore
from PyQt5.QtWidgets import QFrame, QVBoxLayout, QLabel, QProgressBar
from PyQt5.QtGui import QImage, QPixmap


USE_SBMEDIAPLAYER = False


##
## MEDIA PLAYER
##


class SBMediaPlayer(QLabel):
    class State(IntEnum):
        StoppedState = auto()
        PlayingState = auto()
        PausedState = auto()
    StoppedState = State.StoppedState
    PlayingState = State.PlayingState
    PausedState = State.PausedState

    # Signals
    clicked = pyqtSignal(bool)  # Signal includes a boolean to indicate Shift key status
    stateChanged = pyqtSignal(State)

    # Shared data
    volume = 0
    speed = 1.0
    detect_edges = False
    edge_factor = 1.0


    def __init__(self):
        super().__init__()
        self.state = self.State.StoppedState
        self.videoplayer = None

        self.video_path = None
        self.fps = 0
        self.start_frame_index = 0
        self.end_frame_index = 0  # excluded (i.e. next frame's start_frame_index)

        # self.setFixedSize(SHOT_WIDGET_WIDTH, SHOT_WIDGET_HEIGHT)  #  256 x 148 px
        # self.setFrameStyle(QFrame.Box)
        # self.setMaximumSize(SHOT_WIDGET_IMAGE_WIDTH, SHOT_WIDGET_IMAGE_HEIGHT)  #  256 x 128 px
        self.setAlignment(Qt.AlignCenter)
        self.setStyleSheet("background-color: black;")  # black, darkCyan


    def set_video(self, video_path, fps, frame_count):
        self.video_path = video_path
        self.fps = fps
        self.start_frame_index = 0
        self.end_frame_index = frame_count
        self.set_frame(0)


    def set_state(self, state):
        if isinstance(state, self.State):
            if self.state != state:
                self.state = state
                self.stateChanged.emit(state)
                print(f"State changed to: {self.state.name} ({self.state.value})")
        else:
            raise ValueError("Invalid state")


    def get_state(self):
        return self.state


    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            shift_pressed = event.modifiers() & Qt.ShiftModifier
            self.clicked.emit(bool(shift_pressed))


    def play(self):
        if not os.path.isfile(self.video_path):
            print(f"Error: File {self.video_path} does not exist.")
            return

        self.videoplayer = VideoPlayer(self.video_path, self.fps, self.start_frame_index, self.end_frame_index, SBMediaPlayer.volume, SBMediaPlayer.speed, SBMediaPlayer.detect_edges, SBMediaPlayer.edge_factor)
        if self.videoplayer:
            self.videoplayer.frame_signal.connect(self.on_frame_loaded)
            self.videoplayer.start()  # Start the video rendering thread
            self.set_state(self.PlayingState)


    def pause(self):
        if self.videoplayer:
            self.videoplayer.pause()
            self.set_state(self.PausedState)


    def resume(self):
        if self.videoplayer:
            self.videoplayer.resume()
            self.set_state(self.PlayingState)


    def stop(self):
        if self.videoplayer:
            self.videoplayer.stop()
            self.videoplayer = None
            self.set_state(self.StoppedState)


    def set_frame(self, frame_index):
        if not os.path.isfile(self.video_path):
            print(f"Error: File {self.video_path} does not exist.")
            return

        START_POS = frame_index / self.fps  # no offset for FFmpeg

        # Use FFmpeg to extract the frame
        ffmpeg_cmd = ffmpeg.input(self.video_path, ss=START_POS)
        if SBMediaPlayer.detect_edges:
            ffmpeg_cmd = (
                ffmpeg_cmd
                .filter('format', 'gray')  # Convert to grayscale
                .filter('sobel', scale=SBMediaPlayer.edge_factor)  # Edge detection
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
            self.width(),
            self.height(),
            Qt.KeepAspectRatio,
            Qt.SmoothTransformation
        )
        self.setPixmap(scaled_pixmap)


    def closeEvent(self, event):
        event.accept()


    def __del__(self):
        """ Ensure the VideoPlayer is stopped when the object is deleted """
        if self.videoplayer:
            self.videoplayer.stop()


if __name__ == "__main__":
    print(f"\033[91mTHIS MODULE FILE IS NOT MEANT TO BE RUN!\033[0m")
