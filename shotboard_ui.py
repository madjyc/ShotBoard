from shotboard_vid import *

import ffmpeg
import subprocess
import os
from PyQt5.QtCore import Qt, pyqtSignal, QEvent
from PyQt5.QtWidgets import QFrame, QVBoxLayout, QLabel, QProgressBar
from PyQt5.QtGui import QImage, QPixmap


SHOT_WIDGET_SELECT_COLOR = "#f9a825"  # orange
SHOT_WIDGET_IMAGE_WIDTH = 296
SHOT_WIDGET_IMAGE_HEIGHT = 167
SHOT_WIDGET_MARGIN = 4  # margin between the scrollarea frame and the widgets

SHOT_WIDGET_PROGRESSBAR_COLOR = "#3a9ad9" #"#0284eb"  # blue
SHOT_WIDGET_PROGRESSBAR_HEIGHT = 15

SHOT_WIDGET_WIDTH = SHOT_WIDGET_IMAGE_WIDTH + 2 * SHOT_WIDGET_MARGIN + 2
SHOT_WIDGET_HEIGHT = SHOT_WIDGET_IMAGE_HEIGHT + SHOT_WIDGET_PROGRESSBAR_HEIGHT + 2 * SHOT_WIDGET_MARGIN + 2

# frame_thickness = self._scroll_area.frameWidth()
# scrollarea_height = self._scroll_area.viewport().height()
# visible_area_top = self._scroll_area.verticalScrollBar().value()
# visible_area_bottom = visible_area_top + scrollarea_height
# vertical_spacing = self._grid_layout.verticalSpacing()
# widget_height = clip_widget.height()
# row, _, _, _ = self._grid_layout.getItemPosition(index)
# clip_widget_top = SHOT_WIDGET_MARGIN + row * (widget_height + vertical_spacing)
# clip_widget_bottom = clip_widget_top + widget_height

class ShotWidget(QFrame):
    clicked = pyqtSignal(bool)  # Signal includes a boolean to indicate Shift key status

    def __init__(self, video_path, fps, start_frame_index, end_frame_index):
        super().__init__()
        self._videoplayer = None
        self._video_path = video_path
        self._fps = fps
        self._start_frame_index = start_frame_index  # included
        self._end_frame_index = end_frame_index  # excluded (i.e. next frame's start_frame_index)
        self._is_selected = False

        self.setFixedSize(SHOT_WIDGET_WIDTH, SHOT_WIDGET_HEIGHT)  #  256 x 148 px
        self.setFrameStyle(QFrame.Box)

        layout = QVBoxLayout()

        # Create a QLabel to display the image (screenshot) and scale it to 256 x 128 px
        self._image_label = QLabel()
        self._image_label.setMaximumSize(SHOT_WIDGET_IMAGE_WIDTH, SHOT_WIDGET_IMAGE_HEIGHT)  #  256 x 128 px
        self._image_label.setAlignment(Qt.AlignCenter)
        self._image_label.setStyleSheet("background-color: black;")
        self._image_label.installEventFilter(self)
        layout.addWidget(self._image_label)

        # Create a QProgressBar to display the frame index
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

        # Set range and value
        self._frame_progress_bar.setMinimum(start_frame_index)
        self._frame_progress_bar.setMaximum(end_frame_index - 1)
        self._frame_progress_bar.setValue(start_frame_index)

        # Set text format to display the frame index
        self._frame_progress_bar.setFormat("Frame %v")

        layout.addWidget(self._frame_progress_bar)
        self.setLayout(layout)
        layout.setContentsMargins(SHOT_WIDGET_MARGIN, SHOT_WIDGET_MARGIN, SHOT_WIDGET_MARGIN, SHOT_WIDGET_MARGIN)

        self.initialise_thumbnail()


    def get_margins(self):
        return self.layout().contentsMargins()


    def get_start_frame_index(self):
        return self._start_frame_index
    

    def get_end_frame_index(self):
        return self._end_frame_index


    def set_end_frame_index(self, end_frame_index):
        assert end_frame_index > self._start_frame_index
        self._end_frame_index = end_frame_index
        self._frame_progress_bar.setMaximum(end_frame_index - 1)


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
    

    def initialise_thumbnail(self):
        if not os.path.isfile(self._video_path):
            print(f"Error: File {self._video_path} does not exist.")
            return

        # Use ffmpeg to extract the frame
        assert self._fps > 0
        start_pos = self._start_frame_index / self._fps  # no offset for ffmpeg
        command = (
            ffmpeg
            .input(self._video_path, ss=start_pos)
            .output('pipe:', vframes=1, format='image2', vcodec='mjpeg')
            .compile()
        )

        process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        out, err = process.communicate()
        if process.returncode != 0:
            print("Error: Cannot extract frame with ffmpeg.")
            print(err.decode())
            return

        # Load the extracted frame using QImage
        qimg = QImage.fromData(out)
        if qimg.isNull():
            print("Error: Cannot load extracted frame.")
            return

        # Update the image label
        self.update_frame(qimg, self._start_frame_index)


    def on_image_label_enter(self):
        self._videoplayer = VideoPlayer(self._video_path, self._fps, self._start_frame_index, self._end_frame_index)
        self._videoplayer.frame_signal.connect(self.update_frame)
        self._videoplayer.start() # start the video rendering thread


    def on_image_label_leave(self):
        if self._videoplayer:
            self._videoplayer.stop()
            self._videoplayer = None


    def update_frame(self, qimage, frame_index):
        pixmap = QPixmap.fromImage(qimage)
        scaled_pixmap = pixmap.scaled(
            self._image_label.maximumWidth(),
            self._image_label.maximumHeight(),
            Qt.KeepAspectRatio,
            Qt.SmoothTransformation
        )
        self._image_label.setPixmap(scaled_pixmap)
        self._frame_progress_bar.setValue(frame_index)


    def closeEvent(self, event):
        self.on_image_label_leave()
        event.accept()


if __name__ == "__main__":
    print(f"\033[91mTHIS MODULE FILE IS NOT MEANT TO BE RUN!\033[0m")
