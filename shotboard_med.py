from shotboard_vid import *
from shotboard_ui import *

import subprocess
import os
import queue
from enum import IntEnum, auto
from PyQt5.QtCore import Qt, pyqtSignal
from PyQt5.QtWidgets import QLabel, QSizePolicy
from PyQt5.QtGui import QImage, QPixmap


# Image dimension for storage
VIDEO_SIZE_MIN = (296, 167) # h = w / (16/9)


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
        self._state = self.StoppedState
        self._videoplayer = None

        self._video_info = None
        self._frame_index = 0

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
        if self._videoplayer:
            self._videoplayer.set_volume(volume)


    def is_ready(self):
        if not self._video_info or not self._video_info.video_path:
            return False
        
        if not os.path.isfile(self._video_info.video_path):
            print(f"Error: File {self._video_info.video_path} does not exist.")
            return False
        
        return True


    def set_video_info(self, video_info):
        self._video_info = video_info
        self._frame_index = 0
        self.seek(0)


    def set_state(self, state):
        if isinstance(state, self.State):
            if self._state != state:
                self._state = state
                self.stateChanged.emit(state)
                # print(f"State changed to: {self._state.name} ({self._state.value})")
        else:
            raise ValueError("Invalid state")


    def get_state(self):
        return self._state


    def resizeEvent(self, event):
        """Handle resizing by recalculating the pixmap."""
        self.set_still_frame(self._frame_index)
        super().resizeEvent(event)


    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            shift_pressed = event.modifiers() & Qt.ShiftModifier
            self.clicked.emit(bool(shift_pressed))


    def get_frame_index(self):
        return self._frame_index


    def is_playing(self):
        return self._state == self.PlayingState


    def is_paused(self):
        return self._state == self.PausedState


    def is_stopped(self):
        return self._state == self.StoppedState


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

        if self._videoplayer:
            self._videoplayer.stop()
            self._videoplayer = None

        self._videoplayer = VideoPlayer(self._video_info, start_frame_index, self._video_info.frame_count, SBMediaPlayer.volume, SBMediaPlayer.speed, SBMediaPlayer.detect_edges, SBMediaPlayer.edge_factor)
        if self._videoplayer:
            self._videoplayer.frame_loaded.connect(self.on_frame_loaded)
            self._videoplayer.start()  # Start the video rendering thread

        self.set_state(self.PlayingState)


    def pause(self):
        if not self.is_ready():
            return

        if self._videoplayer and self.is_playing():
            self._videoplayer.pause()
        
        self.set_state(self.PausedState)


    def resume(self):
        if not self.is_ready():
            return

        if self._videoplayer:
            if self.is_paused():
                if self._frame_index + 1 < self._video_info.frame_count:
                    self._videoplayer.resume()
                else:
                    self._videoplayer.play()
        else:
            self.play(self._frame_index)
        
        self.set_state(self.PlayingState)


    def stop(self, reset=True):
        if not self.is_ready():
            return

        if self._videoplayer:
            self._videoplayer.stop()
            self._videoplayer = None

        self.set_state(self.StoppedState)
        if reset:
            self.reset_frame()
            self._frame_index = 0
            self.frameChanged.emit(0)


    def set_still_frame(self, frame_index):
        if not self.is_ready():
            return

        if self._videoplayer:
            self._videoplayer.stop()
            self._videoplayer = None

        START_POS = max(0, (frame_index + self._video_info.seek_offset) / self._video_info.fps)  # frame position in seconds

        # Run FFmpeg without showing a console window
        if SBMediaPlayer.detect_edges:
            ffmpeg_cmd = [
                "ffmpeg",
                "-loglevel", "quiet",  # Suppress all FFmpeg logging
                "-ss", str(START_POS),  # Fast seek FIRST
                "-i", self._video_info.video_path,  # Input file AFTER
                "-vframes", "1",  # Number of frames to process
                # "-vf", f"format=gray, sobel=scale={SBMediaPlayer.edge_factor}, negate",  # Convert to grayscale, apply Sobel filter, and invert colors
                "-vf", f"scale=iw*sar:ih,setsar=1,format=gray, sobel=scale={SBMediaPlayer.edge_factor}, negate",  # Correct pixel aspect ratio (PAR), grayscale, Sobel, invert colors
                "-f", "image2",  # Output format
                "-vcodec", "mjpeg",  # Video codec
                "-nostdin",  # Disable interaction on standard input
                "-"  # Output to pipe
            ]
        else:
            ffmpeg_cmd = [
                "ffmpeg",
                "-loglevel", "quiet",  # Suppress all FFmpeg logging
                "-ss", str(START_POS),  # Fast seek FIRST
                "-i", self._video_info.video_path,  # Input file AFTER
                "-vframes", "1",  # Number of frames to process
                "-vf", "scale=iw*sar:ih,setsar=1",  # Correct pixel aspect ratio (PAR) before output
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

        out, err = process.communicate()
        if process.returncode != 0:
            print("Error: Cannot extract frame with FFmpeg.")
            # print(err.decode())
            return

        # Load the extracted frame using QImage
        image = QImage.fromData(out)
        if image.isNull():
            print("Error: Cannot load extracted frame.")
            return

        # Update the image label
        self.update_frame_from_image(frame_index, image)
        self._frame_index = frame_index
        self.frameChanged.emit(frame_index)
        self.set_state(self.PausedState)


    def on_frame_loaded(self):
        assert self._videoplayer
        if not self._videoplayer._frame_queue.empty():
            try:
                frame_index, image = self._videoplayer._frame_queue.get()  # Thread-safe
                self.update_frame_from_image(frame_index, image)
                if frame_index + 1 >= self._video_info.frame_count:
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

        self._frame_index = frame_index
        self.frameChanged.emit(frame_index)


    def closeEvent(self, event):
        if self._videoplayer:
            self._videoplayer.stop()
            self._videoplayer = None
        event.accept()


    def __del__(self):
        """ Ensure the VideoPlayer is stopped when the object is deleted """
        if self._videoplayer:
            self._videoplayer.stop()
            self._videoplayer = None


if __name__ == "__main__":
    print(f"\033[91mTHIS MODULE FILE IS NOT MEANT TO BE RUN!\033[0m")
