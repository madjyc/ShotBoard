import ffmpeg
import numpy as np
import pyaudio
import os
from PyQt5.QtCore import QThread, pyqtSignal, QElapsedTimer, QMutex, QWaitCondition
from PyQt5.QtGui import QImage


#
# AUDIO PLAYER
#


AUDIO_BUFFER_SIZE = 1024  # 4096 by default


class AudioPlayer(QThread):
    """Plays audio in a separate thread using PyAudio."""
    def __init__(self, video_path, start_pos, volume, sync_mutex, sync_condition, parent=None):
        super().__init__(parent)

        self._video_path = video_path
        self._start_pos = start_pos
        self._volume = max(0.0, min(1.0, volume))  # Ensure valid range

        self._running = True
        # self._sync_mutex = sync_mutex
        # self._sync_condition = sync_condition  # Shared condition
        self.ready = True # False  # Flag to track readiness
        self.elapsed_time = 0  # Keep track of playback time
        self._process_mutex = QMutex()  # Add a mutex for process safety

        self._audio = None
        self._audio_stream = None


    def run(self):
        """Starts FFmpeg process for audio and streams it to PyAudio."""
        ## CRITICAL SECTION ####################
        self._process_mutex.lock()  # 🔒
        if not self._running:
            self._process_mutex.unlock()  # 🔓
            return
        # i=-23 → Target integrated loudness (LUFS).
        # tp=-2 → True peak limit (-2 dB to prevent clipping).
        # lra=11 → Loudness range adjustment.
        # measured_I=-self._volume * 23 → Adjust perceived volume dynamically based on self._volume.
        self._process = (
            ffmpeg
            .input(self._video_path, ss=self._start_pos)
            #.filter('volume', f'{self._volume * 2}')
            .filter('loudnorm', i=-23, tp=-2, lra=11, measured_I=-self._volume * 23)  # Set volume (1.0 = 100%, 0.5 = 50%)
            #.filter('loudnorm', i=-10, tp=0, lra=11, measured_I=-23 + (self._volume - 1) * 10)
            .output('pipe:', format='s16le', acodec='pcm_s16le', ac=2, ar=44100)
            #.output('pipe:', format='s16le', acodec='pcm_s16le', ac=2, ar=44100, audio_buffer_size=256)
            #.output('pipe:', format='s16le', acodec='pcm_s16le', ac=2, ar=44100, re=None, audio_buffer_size=256)
            .run_async(pipe_stdout=True, pipe_stderr=True)
        )
        self._audio = pyaudio.PyAudio()
        #self._audio_stream = self._audio.open(format=pyaudio.paInt16, channels=2, rate=44100, output=True)
        self._audio_stream = self._audio.open(format=pyaudio.paInt16, channels=2, rate=44100, output=True, frames_per_buffer=AUDIO_BUFFER_SIZE)
        self._process_mutex.unlock()  # 🔓
        ## END CRITICAL SECTION ################

        # Wait for VideoPlayer to be ready before starting playback
        ## CRITICAL SECTION ####################
        # self._sync_mutex.lock()  # 🔒
        # self.ready = True  # Set the flag
        # self._sync_condition.wait(self._sync_mutex)  # Wait for signal
        # self._sync_mutex.unlock()  # 🔓
        ## END CRITICAL SECTION ################

        timer = QElapsedTimer()
        timer.start()

        while self._running:
            ## CRITICAL SECTION ####################
            self._process_mutex.lock() # 🔒
            if not self._running or self._process.poll() is not None:
                self._process_mutex.unlock()  # 🔓
                break  
            audio_bytes = self._process.stdout.read(AUDIO_BUFFER_SIZE)  # Read audio in chunks
            self._process_mutex.unlock()  # 🔓
            ## END CRITICAL SECTION ################

            if not audio_bytes:
                print("Error: Cannot read audio buffer.")
                break

            self._audio_stream.write(audio_bytes)  # Play audio in real-time
            self.elapsed_time = timer.elapsed() * 0.001  # Convert to seconds

        # Cleanup
        ## CRITICAL SECTION ####################
        self._process_mutex.lock()  # 🔒
        if self._audio_stream:
            self._audio_stream.stop_stream()
            self._audio_stream.close()
            self._audio.terminate()
        if hasattr(self, '_process'):
            self._process.stdout.close()
            self._process.stderr.close()
            self._process.terminate()  # kill
            self._process.wait()
        self._process_mutex.unlock()  # 🔓
        ## END CRITICAL SECTION ################


    def get_audio_time(self):
        """Returns the elapsed playback time in seconds."""
        return self.elapsed_time
    

    def stop(self):
        """Stops audio playback cleanly."""
        self._running = False
        ## CRITICAL SECTION ####################
        self._process_mutex.lock()  # 🔒
        if self._audio_stream:
            self._audio_stream.stop_stream()
            self._audio_stream.close()
            self._audio.terminate()
        if hasattr(self, '_process'):
            self._process.stdout.close()
            self._process.stderr.close()
            self._process.terminate()  # kill
            self._process.wait()
        self._process_mutex.unlock()  # 🔓
        ## END CRITICAL SECTION ################
        self.wait()


#
# VIDEO PLAYER
#


class VideoPlayer(QThread):
    frame_signal = pyqtSignal(QImage, int)  # Signal to send frames to the UI

    def __init__(self, video_path, fps, start_frame_index, end_frame_index, volume, detect_edges, edge_factor, parent=None):
        super().__init__(parent)
        self._video_path = video_path
        self._fps = fps
        self._start_frame_index = start_frame_index
        self._end_frame_index = end_frame_index
        self._volume = volume
        self._detect_edges = detect_edges
        self._edge_factor = edge_factor
        self._audio_thread = None  # Store reference to audio thread
        self._running = True
        self._process_mutex = QMutex()  # Add a mutex for process safety

        # Create shared synchronization objects
        self._sync_mutex = QMutex()
        self._sync_condition = QWaitCondition()

        frame_height, frame_width = self._get_frame_size()
        self._frame_size = (frame_height, frame_width, 3)


    def run(self):
        if not os.path.isfile(self._video_path):
            print(f"Error: File {self._video_path} does not exist.")
            return

        assert self._fps > 0
        start_pos = self._start_frame_index / self._fps
        MAX_TIME_DIFF = 1.0 / self._fps

        # Start video process (only video)
        ## CRITICAL SECTION ####################
        self._process_mutex.lock()  # 🔒
        if not self._running:
            self.safe_disconnect()
            self._process_mutex.unlock()  # 🔓
            return
        if self._detect_edges:
            self._process = (
                ffmpeg
                .input(self._video_path, ss=start_pos)
                .filter('format', 'gray')  # Convert to grayscale
                #.filter('prewitt', scale=1.5)
                .filter('sobel', scale=self._edge_factor)  # Edge detection
                #.filter('edgedetect', mode='canny', low=20/255, high=50/255)  # low=0.02, high=0.1
                .filter('negate')  # Invert colors
                #.filter('eq', contrast=1000.0, brightness=-1.0, gamma=0.1)  # Darken edges (contrast=3.0, brightness=-0.2)
                .output('pipe:', format='rawvideo', pix_fmt='rgb24', vframes=self._end_frame_index - self._start_frame_index)
                .run_async(pipe_stdout=True, pipe_stderr=True)
            )
        else:
            self._process = (
                ffmpeg
                .input(self._video_path, ss=start_pos)
                .output('pipe:', format='rawvideo', pix_fmt='rgb24', vframes=self._end_frame_index - self._start_frame_index)
                .run_async(pipe_stdout=True, pipe_stderr=True)
            )
        self._process_mutex.unlock()  # 🔓
        ## END CRITICAL SECTION ################

        timer = QElapsedTimer()
        TARGET_TIME = 1000 / self._fps  # Desired frame interval in ms

        # Start audio thread
        if self._volume > 0:
            ## CRITICAL SECTION ####################
            self._process_mutex.lock()  # 🔒
            if not self._running:  # In case stop() was called before creating the thread
                self.safe_disconnect()
                self._process_mutex.unlock()  # 🔓
                return
            self._audio_thread = AudioPlayer(self._video_path, start_pos, self._volume, self._sync_mutex, self._sync_condition)
            self._audio_thread.start()
            self._process_mutex.unlock()  # 🔓
            ## END CRITICAL SECTION ################

            # Wait until the audio thread is ready
            ## CRITICAL SECTION ####################
            # self._sync_mutex.lock() # 🔒
            # while not self._audio_thread.ready:  # Wait for audio thread readiness
            #     self._sync_mutex.unlock()  # 🔓
            #     QThread.msleep(1)  # Prevent busy-waiting
            #     self._sync_mutex.lock() # 🔒

            # Signal the AudioPlayer that the video is ready
            # self._sync_condition.wakeAll()
            # self._sync_mutex.unlock()  # 🔓
            ## END CRITICAL SECTION ################

        frame_index = self._start_frame_index
        while self._running and frame_index < self._end_frame_index:
            timer.start()
            
            # Ensure process isn't stopped before reading
            ## CRITICAL SECTION ####################
            self._process_mutex.lock() # 🔒
            if not self._running or self._process.poll() is not None:
                self._process_mutex.unlock()  # 🔓
                break  
            video_bytes = self._process.stdout.read(np.prod(self._frame_size))  # Read frame safely
            self._process_mutex.unlock()  # 🔓
            ## END CRITICAL SECTION ################

            if not video_bytes:
                print("Error: Cannot read frame.")
                break

            if len(video_bytes) == np.prod(self._frame_size):
                frame = np.frombuffer(video_bytes, np.uint8).reshape(self._frame_size)
                h, w, ch = frame.shape
                bytes_per_line = ch * w
                qimg = QImage(frame.data, w, h, bytes_per_line, QImage.Format_RGB888)

                self.frame_signal.emit(qimg, frame_index)
            else:
                print(f"Error: Frame size mismatch. Expected {np.prod(self._frame_size)} bytes, got {len(video_bytes)} bytes.")
                break

            frame_index += 1

            # Compare video time with audio time
            if self._running:
                video_time = (frame_index - self._start_frame_index) / self._fps  # Expected video time in seconds
                audio_time = self._audio_thread.get_audio_time() if self._audio_thread else video_time
                time_diff = video_time - audio_time  # in s

                if time_diff < -MAX_TIME_DIFF:  # Video is behind → drop a frame
                    if frame_index < self._end_frame_index - 2:  # Last frame should always be displayed
                        #print(f"Skipping frame {frame_index} to sync video (diff: {time_diff:.3f}s)")
                        frame_index += 1  # Drop this frame and move to the next
                    continue

                remaining_time = TARGET_TIME - timer.elapsed()  # in ms

                if time_diff > MAX_TIME_DIFF:  # Video is ahead → slow it down
                    #print(f"Slowing down frame {frame_index} to sync video (diff: {time_diff:.3f}s)")
                    remaining_time += time_diff * 1000

                remaining_time = int(max(0, min(remaining_time, TARGET_TIME * 2)))  # clamp to [0, 2 * TARGET_TIME]
                if remaining_time > 0:
                    self.msleep(remaining_time)

        self.cleanup()


    def safe_disconnect(self):
        try:
            self.frame_signal.disconnect()
        except TypeError:
            pass


    def cleanup(self):
        self._running = False
        self.safe_disconnect()
        ## CRITICAL SECTION ####################
        self._process_mutex.lock()  # 🔒
        if self._audio_thread:
            self._audio_thread.stop()
        if hasattr(self, '_process'):
            self._process.stdout.close()
            self._process.stderr.close()
            self._process.terminate()  # kill
            self._process.wait()
        self._process_mutex.unlock()  # 🔓
        ## END CRITICAL SECTION ################


    def _get_frame_size(self):
        probe = ffmpeg.probe(self._video_path)
        video_info = next(stream for stream in probe['streams'] if stream['codec_type'] == 'video')
        width = int(video_info['width'])
        height = int(video_info['height'])
        return height, width
    

    def stop(self):
        self._running = False
        self.safe_disconnect()
        self.quit()
        ## CRITICAL SECTION ####################
        self._process_mutex.lock()  # 🔒
        if self._audio_thread:
            self._audio_thread.stop()
        if hasattr(self, '_process'):
            self._process.stdout.close()
            self._process.stderr.close()
            self._process.terminate()  # kill
            self._process.wait()
        self._process_mutex.unlock()  # 🔓
        ## END CRITICAL SECTION ################
        self.wait()


if __name__ == "__main__":
    print(f"\033[91mTHIS MODULE FILE IS NOT MEANT TO BE RUN!\033[0m")
