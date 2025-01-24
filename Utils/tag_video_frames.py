import cv2
import os
import subprocess
from tkinter import Tk
from tkinter.filedialog import askopenfilename


def add_frame_numbers(input_video_path, temp_output_path, font_scale=1, font_thickness=2):
    # Open the input video
    cap = cv2.VideoCapture(input_video_path)
    if not cap.isOpened():
        print("Error: Could not open input video.")
        return False

    # Get video properties
    frame_width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    frame_height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    fps = cap.get(cv2.CAP_PROP_FPS)

    # Define the codec and create the VideoWriter object for temporary storage
    fourcc = cv2.VideoWriter_fourcc(*'mp4v')  # Temporary uncompressed video
    out = cv2.VideoWriter(temp_output_path, fourcc, fps, (frame_width, frame_height))

    # Loop through each frame
    frame_number = 0
    while cap.isOpened():
        ret, frame = cap.read()
        if not ret:
            break

        # Add frame number to the top-left corner
        text = f"Frame: {frame_number}"
        font = cv2.FONT_HERSHEY_SIMPLEX
        position = (10, 30)  # x, y coordinates of the text
        color = (0, 255, 0)  # Green
        cv2.putText(frame, text, position, font, font_scale, color, font_thickness)

        # Write the frame to the temporary file
        out.write(frame)
        frame_number += 1
        print(f"Processing frame {frame_number}", end="\r")

    # Release resources
    cap.release()
    out.release()
    print("\nFrame tagging complete. Proceeding to compression...")
    return True


def compress_with_ffmpeg(temp_output_path, output_video_path, input_video_path):
    # FFmpeg command to compress the video with similar settings to the input
    ffmpeg_command = [
        "ffmpeg",
        "-y",  # Overwrite output file without asking
        "-i", temp_output_path,  # Input temporary video
        "-i", input_video_path,  # Input original video to copy compression settings
        "-map", "0:v:0",  # Use the video stream from the temp file
        "-map", "1:a?",  # Use the audio stream from the original if available
        "-c:v", "libx264",  # Use the H.264 codec for compression
        "-preset", "medium",  # Balance between compression speed and size
        "-crf", "23",  # Compression rate factor (lower = better quality)
        "-c:a", "aac",  # Audio codec
        "-b:a", "192k",  # Audio bitrate
        output_video_path
    ]

    # Run FFmpeg
    subprocess.run(ffmpeg_command, stdout=subprocess.DEVNULL, stderr=subprocess.STDOUT)
    print(f"Output saved to: {output_video_path}")


def main():
    # Use a file dialog to let the user select the input video
    Tk().withdraw()  # Hide the main tkinter window
    input_video_path = askopenfilename(title="Select a Video File", filetypes=[("Video Files", "*.mp4;*.avi;*.mov;*.mkv")])

    if not input_video_path:
        print("No video file selected. Exiting.")
        return

    # Generate output filenames
    input_basename, input_extension = os.path.splitext(input_video_path)
    temp_output_path = f"{input_basename}_temp.mp4"
    output_video_path = f"{input_basename}_tagged{input_extension}"

    # Process video
    if add_frame_numbers(input_video_path, temp_output_path):
        compress_with_ffmpeg(temp_output_path, output_video_path, input_video_path)

        # Clean up temporary file
        os.remove(temp_output_path)


if __name__ == "__main__":
    main()
