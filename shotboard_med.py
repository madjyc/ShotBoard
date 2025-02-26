from shotboard_vid import *
from shotboard_ui import *

import ffmpeg
import subprocess
import os
import queue
from enum import IntEnum, auto
from PyQt5.QtCore import Qt, pyqtSignal
from PyQt5.QtWidgets import QLabel, QSizePolicy
from PyQt5.QtGui import QImage, QPixmap


# Image dimension for storage
VIDEO_SIZE_MIN = (296, 167) # h = w * 0.5625


##
## MEDIA PLAYER
##


class SBMediaPlayer(QLabel):
    class State(IntEnum):
        StoppedState = auto()
        PlayingState = auto()
        PausedState = auto()
    # Helpers
    StoppedState = State.StoppedState
    PlayingState = State.PlayingState
    PausedState = State.PausedState

    # Signals
    clicked = pyqtSignal(bool)  # Signal includes a boolean to indicate Shift key status
    stateChanged = pyqtSignal(State)
    frameChanged = pyqtSignal(int)

    # Shared data
    volume = 0
    speed = 1.0
    detect_edges = False
    edge_factor = 1.0


    def __init__(self):
        super().__init__()
        self.state = self.StoppedState
        self.videoplayer = None

        self.video_path = None
        self.fps = 0
        self.frame_index = 0
        self.end_frame_index = 0  # excluded

        self.setAlignment(Qt.AlignCenter)
        self.setStyleSheet("background-color: black;")  # black, darkCyan
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.setMinimumSize(VIDEO_SIZE_MIN[0], VIDEO_SIZE_MIN[1])


    def reset_frame(self):
        pixmap = QPixmap(self.size())
        pixmap.fill(Qt.black)
        self.setPixmap(pixmap)        


    def set_volume(self, volume):
        SBMediaPlayer.volume = volume
        if self.videoplayer:
            self.videoplayer.set_volume(volume)


    def is_ready(self):
        if not self.video_path:
            return False
        
        if not os.path.isfile(self.video_path):
            print(f"Error: File {self.video_path} does not exist.")
            return False
        
        return True


    def set_video(self, video_path, fps, frame_count):
        self.video_path = video_path
        self.fps = fps
        self.frame_index = 0
        self.end_frame_index = frame_count
        self.seek(0)


    def set_state(self, state):
        if isinstance(state, self.State):
            if self.state != state:
                self.state = state
                self.stateChanged.emit(state)
                # print(f"State changed to: {self.state.name} ({self.state.value})")
        else:
            raise ValueError("Invalid state")


    def get_state(self):
        return self.state


    def resizeEvent(self, event):
        """Handle resizing by recalculating the pixmap."""
        self.set_still_frame(self.frame_index)
        super().resizeEvent(event)


    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            shift_pressed = event.modifiers() & Qt.ShiftModifier
            self.clicked.emit(bool(shift_pressed))


    def get_frame_index(self):
        return self.frame_index


    def is_playing(self):
        return self.state == self.PlayingState


    def is_paused(self):
        return self.state == self.PausedState


    def is_stopped(self):
        return self.state == self.StoppedState


    def seek(self, frame_index):
        if not self.is_ready():
            return

        # If playing, start playing again from new frame
        if self.is_playing():
            self.play(frame_index)
        else: 
            self.set_still_frame(frame_index)


    def play(self, start_frame_index=0):
        if not self.is_ready():
            return

        if self.videoplayer:
            self.videoplayer.stop()
            self.videoplayer = None

        self.videoplayer = VideoPlayer(self.video_path, self.fps, start_frame_index, self.end_frame_index, SBMediaPlayer.volume, SBMediaPlayer.speed, SBMediaPlayer.detect_edges, SBMediaPlayer.edge_factor)
        if self.videoplayer:
            self.videoplayer.frame_signal.connect(self.on_frame_loaded)
            self.videoplayer.start()  # Start the video rendering thread

        self.set_state(self.PlayingState)


    def pause(self):
        if not self.is_ready():
            return

        if self.videoplayer and self.is_playing():
            self.videoplayer.pause()
        
        self.set_state(self.PausedState)


    def resume(self):
        if not self.is_ready():
            return

        if self.videoplayer:
            if self.is_paused():
                if self.frame_index + 1 < self.end_frame_index:
                    self.videoplayer.resume()
                else:
                    self.videoplayer.play()
        else:
            self.play(self.frame_index)
        
        self.set_state(self.PlayingState)


    def stop(self, reset=True):
        if not self.is_ready():
            return

        if self.videoplayer:
            self.videoplayer.stop()
            self.videoplayer = None

        self.set_state(self.StoppedState)
        if reset:
            self.reset_frame()
            self.frame_index = 0
            self.frameChanged.emit(0)


    def set_still_frame(self, frame_index):
        if not self.is_ready():
            return

        if self.videoplayer:
            self.videoplayer.stop()
            self.videoplayer = None

        START_POS = (frame_index - 0.5) / self.fps

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
        self.update_frame_from_image(frame_index, image)
        self.frame_index = frame_index
        self.frameChanged.emit(frame_index)
        self.set_state(self.PausedState)


    def on_frame_loaded(self):
        assert self.videoplayer
        if not self.videoplayer._frame_queue.empty():
            try:
                frame_index, image = self.videoplayer._frame_queue.get()  # Thread-safe
                self.update_frame_from_image(frame_index, image)
                if frame_index + 1 >= self.end_frame_index:
                    self.stop(False)
            except queue.Empty:
                print("Warning: Frame queue is empty. Skipping frame.")
                return
            except Exception as e:
                print(f"Error retrieving frame from queue: {e}")
                return


    def update_frame_from_image(self, frame_index, image):
        self.update_frame(frame_index, QPixmap.fromImage(image))


    def update_frame(self, frame_index, pixmap):
        scaled_pixmap = pixmap.scaled(self.width(), self.height(), Qt.KeepAspectRatio, Qt.FastTransformation)  # /!\ Qt.SmoothTransformation stalls when ThreadPoll is running
        self.setPixmap(scaled_pixmap)

        self.frame_index = frame_index
        self.frameChanged.emit(frame_index)


    def closeEvent(self, event):
        if self.videoplayer:
            self.videoplayer.stop()
            self.videoplayer = None
        event.accept()


    def __del__(self):
        """ Ensure the VideoPlayer is stopped when the object is deleted """
        if self.videoplayer:
            self.videoplayer.stop()
            self.videoplayer = None


if __name__ == "__main__":
    print(f"\033[91mTHIS MODULE FILE IS NOT MEANT TO BE RUN!\033[0m")
