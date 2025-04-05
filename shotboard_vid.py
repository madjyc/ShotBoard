import ffmpeg
import subprocess
import numpy as np
import pyaudio
from queue import Queue
import os
import sys
from math import *
from PyQt5.QtCore import QThread, pyqtSignal, QElapsedTimer, QMutex, QMutexLocker, QWaitCondition
from PyQt5.QtGui import QImage


# Platform-specific settings
FFMPEG_NOWINDOW_KWARGS = {}
if sys.platform == "win32":
    startupinfo = subprocess.STARTUPINFO()
    startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    startupinfo.wShowWindow = subprocess.SW_HIDE
    FFMPEG_NOWINDOW_KWARGS["startupinfo"] = startupinfo


MAX_VOLUME_FACTOR = 2.0


#
# VIDEO INFO
#


class VideoInfo():
    def __init__(self):
        self.clear_info()


    def clear_info(self):
        self.video_path = None
        self.frame_width = 0
        self.frame_height = 0
        self.display_width = 0
        self.fps = 0
        self.frame_count = 0
        self.duration = 0  # in seconds
        self.seek_offset = 0.0  # in frames


    def set_from_video(self, video_path, seek_offset = 0.0):
        probe = ffmpeg.probe(video_path)
        video_info = next(stream for stream in probe['streams'] if stream['codec_type'] == 'video')
        
        frame_width = int(video_info['width'])
        frame_height = int(video_info['height'])
        fps = eval(video_info.get('r_frame_rate', '0'))

        duration = float(probe['format'].get('duration', 0))  # in seconds
        frame_count = video_info.get('nb_frames')
        if frame_count is None or frame_count == "0":
            frame_count = int(fps * duration) if fps > 0 and duration > 0 else 0
        else:
            frame_count = int(frame_count)

        # Get PAR (Pixel Aspect Ratio), default to "1:1" if missing
        par_str = video_info.get('sample_aspect_ratio', '1:1')
        par_w, par_h = map(int, par_str.split(':'))
        display_width = int(frame_width * (par_w / par_h))

        self.video_path = video_path
        self.frame_width = frame_width
        self.frame_height = frame_height
        self.display_width = display_width
        self.fps = fps
        self.frame_count = frame_count
        self.duration = duration
        self.seek_offset = seek_offset


#
# AUDIO PLAYER
#


AUDIO_BUFFER_SIZE = 1024  # E.g. (44100 * 2 * 2) = 1 second of audio (44.1kHz, 16-bit stereo)


class AudioPlayer(QThread):
    """Plays audio in a separate thread using PyAudio."""
    audio = None
    audio_stream = None

    def __init__(self, video_info, start_pos, volume, speed, parent=None):
        super().__init__(parent)

        if AudioPlayer.audio is None:
            AudioPlayer.audio = pyaudio.PyAudio()
        if AudioPlayer.audio_stream is None:
            AudioPlayer.audio_stream = AudioPlayer.audio.open(
                format=pyaudio.paInt16, channels=2, rate=44100, output=True, frames_per_buffer=AUDIO_BUFFER_SIZE
            )

        self._video_info = video_info
        self._start_pos = start_pos
        self.set_volume(volume)  # Ensure valid range
        self._speed = speed

        self._running = True
        self._paused = False
        self._process_mutex = QMutex()
        self._pause_mutex = QMutex()
        self._pause_condition = QWaitCondition()
        self._pause_duration_ms = 0
        self._master_clock_timer = QElapsedTimer()


    def run(self):
        """Starts FFmpeg process for audio and streams it to PyAudio."""
        with QMutexLocker(self._process_mutex):  # 🔒
            if not self._running:
                return

            # self._process = (
            #     ffmpeg
            #     .input(self._video_info.video_path, ss=self._start_pos)
            #     .output('pipe:', format='s16le', acodec='pcm_s16le', ac=2, ar=44100)
            #     .run_async(pipe_stdout=True, pipe_stderr=False)
            #             )

            # FFmpeg command as a plain list of arguments
            ffmpeg_cmd = [
                "ffmpeg",
                "-loglevel", "quiet",  # Suppress all FFmpeg logging
                "-ss", str(self._start_pos),  # Fast seek to the start position
                "-i", self._video_info.video_path,  # Input file
                "-f", "s16le",  # Output format (16-bit signed little-endian PCM)
                "-acodec", "pcm_s16le",  # Audio codec
                "-ac", "2",  # Number of audio channels (stereo)
                "-ar", "44100",  # Audio sample rate (44.1 kHz)
                "-"  # Output to pipe
            ]

            # Run FFmpeg without showing a console window
            self._process = subprocess.Popen(
                ffmpeg_cmd,
                stdout=subprocess.PIPE,  # Capture stdout
                stderr=subprocess.DEVNULL,  # Discard stderr
                **FFMPEG_NOWINDOW_KWARGS
            )

        self._master_clock_timer.start()

        while self._running:
            with QMutexLocker(self._pause_mutex):  # 🔒
                if self._paused:
                    pause_time_ms = self._master_clock_timer.elapsed()
                    self._pause_condition.wait(self._pause_mutex)
                    self._pause_duration_ms += self._master_clock_timer.elapsed() - pause_time_ms

            with QMutexLocker(self._process_mutex):  # 🔒
                if not self._running or self._process.poll() is not None:
                    break
                try:
                    audio_bytes = self._process.stdout.read(AUDIO_BUFFER_SIZE)
                    if not audio_bytes:
                        raise RuntimeError("Error: No audio data received from FFmpeg.")
                except (OSError, ValueError) as e:
                    print(f"Error reading audio data: {e}")
                    audio_bytes = None
                    break

            try:
                # Apply a linear volume factor
                audio_samples = ((np.frombuffer(audio_bytes, dtype=np.int16) * self._volume).astype(np.int16))
                
                # Write the adjusted audio data to the stream
                self.audio_stream.write(audio_samples.tobytes())
            except (OSError) as e:
                print(f"Error playing audio data: {e}")
                break

        self.cleanup()


    def cleanup(self):
        self._running = False
        with QMutexLocker(self._process_mutex):  # 🔒
            # if AudioPlayer.audio_stream:
            #     AudioPlayer.audio_stream.stop_stream()
            #     AudioPlayer.audio_stream.close()
            #     AudioPlayer.audio.terminate()
            if hasattr(self, '_process'):
                self._process.stdout.close()
                self._process.terminate()  # kill
                self._process.wait()


    def set_volume(self, volume):
        """Dynamically change the audio volume."""
        self._volume = max(0.0, min(MAX_VOLUME_FACTOR, volume))  # Limit volume between 0% and 100% of MAX_VOLUME_FACTOR


    def get_volume(self):
        return self._volume


    def get_elapsed_time_ms(self):
        """Returns the elapsed playback time in seconds since the beginning of the audio stream rendition."""
        elapsed_time = self._master_clock_timer.elapsed() - self._pause_duration_ms  # in ms
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
    frame_loaded = pyqtSignal()  # Signal to send frames to the UI

    def __init__(self, video_info, start_frame_index, end_frame_index, volume, speed, detect_edges, edge_factor, parent=None):
        super().__init__(parent)
        self._audio_thread = None  # Store reference to audio thread

        self._video_info = video_info
        self._frame_size = (video_info.frame_height, video_info.display_width, 3)
        self._start_frame_index = start_frame_index
        self._end_frame_index = end_frame_index
        self.set_volume(volume)  # Ensure valid range
        self._speed = max(0.5, min(2.0, speed))  # Limit to [0.5x, 2.0x]
        self._detect_edges = detect_edges
        self._edge_factor = edge_factor

        self._running = True
        self._paused = False
        self._process_mutex = QMutex()
        self._pause_mutex = QMutex()
        self._pause_condition = QWaitCondition()

        self._frame_queue = Queue(maxsize=5)  # Thread-safe queue


    def run(self):
        if not os.path.isfile(self._video_info.video_path):
            print(f"Error: File {self._video_info.video_path} does not exist.")
            return

        assert self._video_info.fps > 0
        START_POS = max(0, (self._start_frame_index + self._video_info.seek_offset) / self._video_info.fps)  # frame position in seconds
        FRAME_BYTES = np.prod(self._frame_size)  # shortcut for: w * h *ch

        # Start video process (only video)
        with QMutexLocker(self._process_mutex):  # 🔒
            if not self._running:
                self.safe_disconnect()
                return

        # Run FFmpeg without showing a console window
        if self._detect_edges:
            ffmpeg_cmd = [
                "ffmpeg",
                "-loglevel", "quiet",  # Suppress all FFmpeg logging
                "-ss", str(START_POS),  # Fast seek FIRST
                "-i", self._video_info.video_path,  # Input file AFTER
                "-vframes", str(self._end_frame_index - self._start_frame_index),  # Number of frames to process
                # "-vf", f"format=gray, sobel=scale={self._edge_factor}, negate",  # Convert to grayscale, apply Sobel filter, and invert colors
                "-vf", f"scale=iw*sar:ih,setsar=1,format=gray, sobel=scale={self._edge_factor}, negate",  # Correct pixel aspect ratio (PAR), grayscale, Sobel, invert colors
                "-f", "rawvideo",  # Output format
                "-pix_fmt", "rgb24",  # Pixel format
                "-nostdin",  # Disable interaction on standard input
                "-"  # Output to pipe
            ]
        else:
            ffmpeg_cmd = [
                "ffmpeg",
                "-loglevel", "quiet",  # Suppress all FFmpeg logging
                "-ss", str(START_POS),  # Fast seek FIRST
                "-i", self._video_info.video_path,  # Input file AFTER
                "-vframes", str(self._end_frame_index - self._start_frame_index),  # Number of frames to process
                "-vf", "scale=iw*sar:ih,setsar=1",  # Correct pixel aspect ratio (PAR) before output
                "-f", "rawvideo",  # Output format
                "-pix_fmt", "rgb24",  # Pixel format
                "-nostdin",  # Disable interaction on standard input
                "-"  # Output to pipe
            ]

        self._process = subprocess.Popen(ffmpeg_cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, **FFMPEG_NOWINDOW_KWARGS)

        TARGET_TIME_MS = 1000 / self._video_info.fps  # Desired frame interval in ms

        # Start audio thread
        with QMutexLocker(self._process_mutex):  # 🔒
            if not self._running:  # In case stop() was called before creating the thread
                self.safe_disconnect()
                return
            self._audio_thread = AudioPlayer(self._video_info, START_POS, self._volume, self._speed)
            self._audio_thread.start()

        frame_timer = QElapsedTimer()

        frame_index = self._start_frame_index
        time_compensation_ms = 0
        while self._running and frame_index < self._end_frame_index:
            frame_timer.start()
            pause_duration_ms = 0
            
            with QMutexLocker(self._pause_mutex):  # 🔒
                if self._paused:
                    self._pause_condition.wait(self._pause_mutex)  # Wait until resumed
                    pause_duration_ms = frame_timer.elapsed()

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
                self.frame_loaded.emit()  # Notify UI to update
            else:
                print(f"Image queue full: Dropping frame {frame_index}.")

            frame_index += 1

            # Compare video time with audio time
            if self._running:
                remaining_time_ms = TARGET_TIME_MS - (frame_timer.elapsed() - pause_duration_ms) + time_compensation_ms
                remaining_time_ms = int(max(0, min(TARGET_TIME_MS * 2, remaining_time_ms)))  # clamp to [0, 2 * TARGET_TIME_MS] -> max 2 frames

                # Sleep until end of frame
                if remaining_time_ms > 0:
                    self.msleep(remaining_time_ms)

                # Absolute time difference at the end of the frame, to compensate next frame
                video_time_ms = (frame_index - self._start_frame_index) * TARGET_TIME_MS  # Expected video time in ms
                master_clock_elapsed_time_ms = self._audio_thread.get_elapsed_time_ms() if self._audio_thread else video_time_ms
                time_compensation_ms = video_time_ms - master_clock_elapsed_time_ms

        self.cleanup()


    def read_one_frame(self, frame_bytes):
        with QMutexLocker(self._process_mutex):  # 🔒
            if not self._running or self._process.poll() is not None:
                print("Skipping next frame: FFmpeg still busy decoding")
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
            self.frame_loaded.disconnect()
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
    

    def set_volume(self, volume):
        """Dynamically change FFmpeg audio volume."""
        self._volume = max(0.0, min(MAX_VOLUME_FACTOR, volume))  # Limit volume between 0% and 100% of MAX_VOLUME_FACTOR
        if self._audio_thread:
            self._audio_thread.set_volume(self._volume)


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
