import sys
import subprocess
import time
from PyQt5.QtWidgets import QApplication, QWidget, QLabel, QPushButton, QVBoxLayout, QFileDialog
from PyQt5.QtCore import Qt


class VideoPlayer(QWidget):
    def __init__(self):
        super().__init__()

        self.setWindowTitle("FFplay in PyQt5 QLabel")
        self.setGeometry(100, 100, 800, 600)

        # QLabel as a placeholder for the video
        self.video_label = QLabel(self)
        self.video_label.setStyleSheet("background-color: black;")
        self.video_label.setAlignment(Qt.AlignCenter)

        # Buttons
        self.play_button = QPushButton("Open & Play", self)
        self.play_button.clicked.connect(self.play_video)

        self.stop_button = QPushButton("Stop", self)
        self.stop_button.clicked.connect(self.stop_video)

        # Layout
        layout = QVBoxLayout()
        layout.addWidget(self.video_label)
        layout.addWidget(self.play_button)
        layout.addWidget(self.stop_button)
        self.setLayout(layout)

        self.process = None  # To store ffplay process


    def play_video(self):
        file_path, _ = QFileDialog.getOpenFileName(self, "Open Video File", "", "MP4 Files (*.mp4);;All Files (*)")
        if not file_path:
            return

        # Stop any existing playback
        self.stop_video()

        # Launch ffplay
        self.process = subprocess.Popen([
            "ffplay", "-i", file_path,
            "-noborder"  # Remove window decorations
        ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

        # Start tracking ffplay's window position after a short delay
        time.sleep(0.5)  # Allow ffplay time to open


    def stop_video(self):
        if self.process:
            self.process.terminate()
            self.process = None


if __name__ == "__main__":
    app = QApplication(sys.argv)
    player = VideoPlayer()
    player.show()
    sys.exit(app.exec_())
