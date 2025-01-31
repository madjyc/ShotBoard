import ffmpeg
import numpy as np
import os
from PyQt5.QtCore import QThread, pyqtSignal
from PyQt5.QtGui import QImage


class VideoPlayer(QThread):
    frame_signal = pyqtSignal(QImage, int)  # Signal to send frames to the UI

    def __init__(self, video_path, fps, start_frame_index, end_frame_index, parent=None):
        super().__init__(parent)
        self._video_path = video_path
        self._fps = fps
        self._start_frame_index = start_frame_index
        self._end_frame_index = end_frame_index
        self._running = True


    def run(self):
        if not os.path.isfile(self._video_path):
            print(f"Error: File {self._video_path} does not exist.")
            return

        assert self._fps > 0
        start_pos = (self._start_frame_index + 0.5) / self._fps
        self.process = (
            ffmpeg
            .input(self._video_path, ss=start_pos)
            .output('pipe:', format='rawvideo', pix_fmt='rgb24', vframes=self._end_frame_index - self._start_frame_index)
            .run_async(pipe_stdout=True)#, stderr=subprocess.DEVNULL)
        )

        frame_height, frame_width = self._get_frame_size()
        frame_size = (frame_height, frame_width, 3)
        sleep_time = int(1000 / self._fps)

        frame_index = self._start_frame_index
        while self._running and frame_index < self._end_frame_index:
            in_bytes = self.process.stdout.read(np.prod(frame_size))
            if not in_bytes:
                print("Error: Cannot read frame.")
                break

            if len(in_bytes) == np.prod(frame_size):
                frame = np.frombuffer(in_bytes, np.uint8).reshape(frame_size)
                h, w, ch = frame.shape
                bytes_per_line = ch * w
                qimg = QImage(frame.data, w, h, bytes_per_line, QImage.Format_RGB888)

                self.frame_signal.emit(qimg, frame_index)
            else:
                print(f"Error: Frame size mismatch. Expected {np.prod(frame_size)} bytes, got {len(in_bytes)} bytes.")
                break

            frame_index += 1
            if self._running:
                self.msleep(sleep_time)

        self.frame_signal.disconnect()
        self.process.stdout.close()
        self.process.wait()


    def _get_frame_size(self):
        probe = ffmpeg.probe(self._video_path)
        video_info = next(stream for stream in probe['streams'] if stream['codec_type'] == 'video')
        width = int(video_info['width'])
        height = int(video_info['height'])
        return height, width


    def stop(self):
        self._running = False
        if hasattr(self, 'process'):
            self.process.terminate()
            self.process.wait()
        self.wait()


if __name__ == "__main__":
    print(f"\033[91mTHIS MODULE FILE IS NOT MEANT TO BE RUN!\033[0m")
