import ffmpeg
import numpy as np
import pyaudio
from queue import Queue
import os
from PyQt5.QtCore import QThread, pyqtSignal, QElapsedTimer, QMutex, QMutexLocker, QWaitCondition
from PyQt5.QtGui import QImage


#
# AUDIO PLAYER
#


AUDIO_BUFFER_SIZE = 1024  # 4096 by default


class AudioPlayer(QThread):
    """Plays audio in a separate thread using PyAudio."""
    _audio = None
    _audio_stream = None

    def __init__(self, video_path, start_pos, volume, speed, parent=None):
        super().__init__(parent)

        if AudioPlayer._audio is None:
            AudioPlayer._audio = pyaudio.PyAudio()
        if AudioPlayer._audio_stream is None:
            AudioPlayer._audio_stream = AudioPlayer._audio.open(
                format=pyaudio.paInt16, channels=2, rate=44100, output=True, frames_per_buffer=AUDIO_BUFFER_SIZE
            )

        self._video_path = video_path
        self._start_pos = start_pos
        self._volume = volume
        self._speed = speed

        self._running = True
        self._paused = False
        self._process_mutex = QMutex()
        self._pause_mutex = QMutex()
        self._pause_condition = QWaitCondition()
        self._pause_duration = 0
        self._audio_timer = QElapsedTimer()


    def run(self):
        """Starts FFmpeg process for audio and streams it to PyAudio."""
        with QMutexLocker(self._process_mutex):  # 🔒
            if not self._running:
                return
            # i=-23 → Target integrated loudness (LUFS).
            # tp=-2 → True peak limit (-2 dB to prevent clipping).
            # lra=11 → Loudness range adjustment.
            # measured_I=-self._volume * 23 → Adjust perceived volume dynamically based on self._volume.
            self._process = (
                ffmpeg
                .input(self._video_path, ss=self._start_pos)
                .filter('volume', f'{self._volume}', enable='between(t,0,99999)')  # Enable commands
                # .filter('loudnorm', i=-23, tp=-2, lra=11, measured_I=-self._volume * 23)  # Set volume (1.0 = 100%, 0.5 = 50%)
                #    .filter('loudnorm', i=-10, tp=0, lra=11, measured_I=-23 + (self._volume - 1) * 10)
                #    .filter('atempo', self._speed)  # DO NOT USE AS LONG AS VIDEO FILTER SETPTS DOOESN'T WORK PROPERLY
                .output('pipe:', format='s16le', acodec='pcm_s16le', ac=2, ar=44100)
                #    .output('pipe:', format='s16le', acodec='pcm_s16le', ac=2, ar=44100, audio_buffer_size=256)
                #    .output('pipe:', format='s16le', acodec='pcm_s16le', ac=2, ar=44100, re=None, audio_buffer_size=256)
                .run_async(pipe_stdin=True, pipe_stdout=True, pipe_stderr=False)
            )

        self._audio_timer.start()

        while self._running:
            with QMutexLocker(self._pause_mutex):  # 🔒
                if self._paused:
                    pause_time = self._audio_timer.elapsed()
                    self._pause_condition.wait(self._pause_mutex)
                    self._pause_duration += self._audio_timer.elapsed() - pause_time

            with QMutexLocker(self._process_mutex):  # 🔒
                if not self._running or self._process.poll() is not None:
                    break
                try:
                    audio_bytes = self._process.stdout.read(AUDIO_BUFFER_SIZE)  # Read audio in chunks
                    if not audio_bytes:
                        raise RuntimeError("Error: No audio data received from FFmpeg.")
                except (OSError, ValueError) as e:
                    print(f"Error reading audio data: {e}")
                    audio_bytes = None
                    break

            try:
                self._audio_stream.write(audio_bytes)  # Play audio in real-time
            except (OSError) as e:
                print(f"Error playing audio data: {e}")
                break

        self.cleanup()


    def cleanup(self):
        self._running = False
        with QMutexLocker(self._process_mutex):  # 🔒
            # if AudioPlayer._audio_stream:
            #     AudioPlayer._audio_stream.stop_stream()
            #     AudioPlayer._audio_stream.close()
            #     AudioPlayer._audio.terminate()
            if hasattr(self, '_process'):
                self._process.stdin.close()
                self._process.stdout.close()
                # self._process.stderr.close()
                self._process.terminate()  # kill
                self._process.wait()


    # /!\ DOESN'T WORK YET
    def set_volume(self, volume):
        """Dynamically change FFmpeg audio volume."""
        self._volume = max(0.0, min(2.0, volume))  # Limit volume between 0% and 200%
        if self._process and self._process.stdin:
            cmd = f"volume {self._volume}\n"  # Send FFmpeg command
            try:
                self._process.stdin.write(cmd.encode())
                self._process.stdin.flush()
            except (OSError, BrokenPipeError) as e:
                print(f"Error sending volume command to FFmpeg: {e}")


    def get_elapsed_time(self):
        """Returns the elapsed playback time in seconds since the beginning of the audio stream rendition."""
        elapsed_time = (self._audio_timer.elapsed() - self._pause_duration) * 0.001  # Adjust for pause and convert to seconds
        return elapsed_time


    def pause(self):
        """Pauses audio playback."""
        with QMutexLocker(self._pause_mutex):
            self._paused = True


    def resume(self):
        """Resumes audio playback."""
        with QMutexLocker(self._pause_mutex):
            self._paused = False
            self._pause_condition.wakeAll()  # Resume the thread


    def stop(self):
        """Stops audio playback."""
        with QMutexLocker(self._process_mutex):
            self._running = False

        with QMutexLocker(self._pause_mutex):
            self._paused = False
            self._pause_condition.wakeAll()

        self.cleanup()
        self.wait()


#
# VIDEO PLAYER
#


class VideoPlayer(QThread):
    frame_signal = pyqtSignal()  # Signal to send frames to the UI

    def __init__(self, video_path, fps, start_frame_index, end_frame_index, volume, speed, detect_edges, edge_factor, parent=None):
        super().__init__(parent)
        self._video_path = video_path
        self._fps = fps
        self._start_frame_index = start_frame_index
        self._end_frame_index = end_frame_index
        self._volume = max(0.0, min(1.0, volume))  # Ensure valid range
        self._speed = max(0.5, min(2.0, speed))  # Limit to [0.5x, 2.0x]
        self._detect_edges = detect_edges
        self._edge_factor = edge_factor

        self._audio_thread = None  # Store reference to audio thread
        self._running = True
        self._paused = False
        self._process_mutex = QMutex()
        self._pause_mutex = QMutex()
        self._pause_condition = QWaitCondition()

        self._frame_queue = Queue(maxsize=5)  # Thread-safe queue

        frame_width, frame_height = self.get_frame_size()
        self._frame_size = (frame_height, frame_width, 3)


    def run(self):
        if not os.path.isfile(self._video_path):
            print(f"Error: File {self._video_path} does not exist.")
            return

        assert self._fps > 0
        START_POS = self._start_frame_index / self._fps
        FRAME_BYTES = np.prod(self._frame_size)  # w * h *ch
        MAX_TIME_DIFF = 1 / self._fps  # 1 frame in seconds

        # Start video process (only video)
        with QMutexLocker(self._process_mutex):  # 🔒
            if not self._running:
                self.safe_disconnect()
                return
            if self._detect_edges:
                self._process = (
                    ffmpeg
                    .input(self._video_path, ss=START_POS)
                    .filter('format', 'gray')  # Convert to grayscale
                    #.filter('prewitt', scale=1.5)
                    .filter('sobel', scale=self._edge_factor)  # Edge detection
                    #.filter('edgedetect', mode='canny', low=20/255, high=50/255)  # low=0.02, high=0.1
                    .filter('negate')  # Invert colors
                    #.filter('eq', contrast=1000.0, brightness=-1.0, gamma=0.1)  # Darken edges (contrast=3.0, brightness=-0.2)
                    # .filter('setpts', f'PTS/{self._speed}')  # DO NOT USE, SYNC ISSUE
                    .output('pipe:', format='rawvideo', pix_fmt='rgb24', vframes=self._end_frame_index - self._start_frame_index)
                    .run_async(pipe_stdout=True, pipe_stderr=False)
                )
            else:
                self._process = (
                    ffmpeg
                    .input(self._video_path, ss=START_POS)
                    # .filter('setpts', f'PTS/{self._speed}')  # DO NOT USE, SYNC ISSUE
                    .output('pipe:', format='rawvideo', pix_fmt='rgb24', vframes=self._end_frame_index - self._start_frame_index)
                    .run_async(pipe_stdout=True, pipe_stderr=False)
                )

        timer = QElapsedTimer()
        TARGET_TIME = 1000 / self._fps  # Desired frame interval in ms

        # Start audio thread
        if self._volume > 0:
            with QMutexLocker(self._process_mutex):  # 🔒
                if not self._running:  # In case stop() was called before creating the thread
                    self.safe_disconnect()
                    return
                self._audio_thread = AudioPlayer(self._video_path, START_POS, self._volume, self._speed)
                self._audio_thread.start()

        frame_index = self._start_frame_index
        while self._running and frame_index < self._end_frame_index:
            timer.start()
            pause_duration = 0
            
            with QMutexLocker(self._pause_mutex):  # 🔒
                if self._paused:
                    self._pause_condition.wait(self._pause_mutex)  # Wait until resumed
                    pause_duration = timer.elapsed()

            # Ensures process isn't stopped before reading
            video_bytes = self.read_one_frame(FRAME_BYTES)
            if not video_bytes:
                break

            frame = np.frombuffer(video_bytes, np.uint8).reshape(self._frame_size)
            h, w, ch = frame.shape
            bytes_per_line = ch * w
            image = QImage(frame.data, w, h, bytes_per_line, QImage.Format_RGB888)

            if not self._frame_queue.full():
                self._frame_queue.put((frame_index, image))  # Thread-safe
                self.frame_signal.emit()  # Notify UI to update
            else:
                print(f"Image queue full: Dropping frame {frame_index}.")

            frame_index += 1

            # Compare video time with audio time
            if self._running:
                video_time = (frame_index - self._start_frame_index) / self._fps  # Expected video time in seconds
                audio_time = self._audio_thread.get_elapsed_time() if self._audio_thread else video_time
                time_diff = video_time - audio_time  # in s

                if time_diff < -MAX_TIME_DIFF:  # Video is behind → drop a frame
                    if frame_index + 1 < self._end_frame_index:  # Last frame should always be displayed
                        video_bytes = self.read_one_frame(FRAME_BYTES)
                        if not video_bytes:
                            break
                        print(f"Dropped frame {frame_index}")
                        frame_index += 1  # Drop this frame and move to the next
                    continue

                remaining_time = TARGET_TIME - (timer.elapsed() - pause_duration) # in ms

                if time_diff > MAX_TIME_DIFF:  # Video is ahead → slow it down
                    remaining_time += time_diff * 1000

                remaining_time = int(max(0, min(TARGET_TIME * 2, remaining_time)))  # clamp to [0, 2 * TARGET_TIME] -> max 2 frames
                if remaining_time > 0:
                    self.msleep(remaining_time)  # in ms

        self.cleanup()


    def read_one_frame(self, frame_bytes):
        with QMutexLocker(self._process_mutex):  # 🔒
            if not self._running or self._process.poll() is not None:
                print("Error: Cannot read frame.")
                return None
            
            try:
                video_bytes = self._process.stdout.read(frame_bytes)
                if not video_bytes:
                    raise RuntimeError("Error: No video data received from FFmpeg.")
            except (OSError, ValueError) as e:
                print(f"Error reading video data: {e}")
                return None

        if len(video_bytes) != frame_bytes:
            print(f"Error: Frame size mismatch. Expected {frame_bytes} bytes, got {len(video_bytes)} bytes.")
            return None

        return video_bytes


    def safe_disconnect(self):
        try:
            self.frame_signal.disconnect()
        except TypeError:
            pass


    def cleanup(self):
        self._running = False
        self.safe_disconnect()
        with QMutexLocker(self._process_mutex):  # 🔒
            if self._audio_thread:
                self._audio_thread.stop()
            if hasattr(self, '_process'):
                self._process.stdout.close()
                # self._process.stderr.close()
                self._process.terminate()  # kill
                self._process.wait()


    def get_frame_size(self):
        probe = ffmpeg.probe(self._video_path)
        video_info = next(stream for stream in probe['streams'] if stream['codec_type'] == 'video')
        width = int(video_info['width'])
        height = int(video_info['height'])
        return width, height


    def set_volume(self, volume):
        """Dynamically change FFmpeg audio volume."""
        if self._audio_thread:
            self._audio_thread.set_volume(volume)


    def pause(self):
        """Pauses video playback."""
        with QMutexLocker(self._pause_mutex):
            self._paused = True

        if self._audio_thread:
            self._audio_thread.pause()


    def resume(self):
        """Resumes video playback."""
        with QMutexLocker(self._pause_mutex):
            self._paused = False
            self._pause_condition.wakeAll()

        if self._audio_thread:
            self._audio_thread.resume()


    def stop(self):
        """Stops video playback."""
        with QMutexLocker(self._process_mutex):
            self._running = False

        with QMutexLocker(self._pause_mutex):
            self._paused = False
            self._pause_condition.wakeAll()

        if self._audio_thread:
            self._audio_thread.stop()

        self.cleanup()
        self.wait()


if __name__ == "__main__":
    print(f"\033[91mTHIS MODULE FILE IS NOT MEANT TO BE RUN!\033[0m")
