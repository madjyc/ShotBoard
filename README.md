![License](https://img.shields.io/badge/license-MIT-green.svg)
![Godot Version](https://img.shields.io/badge/Python-3.10.6-orange.svg)
![Version](https://img.shields.io/badge/version-v0.1.2-blue.svg)

# ShotBoard v0.1.2
For **Windows** 10+ and **Linux** (not sure about **Mac**, feel free to try).

### ShotBoard: Visualize movies shot by shot

**ShotBoard** is a Python application designed for **filmmakers**, **editors**, **storyboarders** and **enthusiasts** alike, who want to explore the structural breakdown of a movie.

With **ShotBoard**, you can:
- Open a movie file (MP4) and detect its individual shots automatically.
- Display those shots as thumbnails in a grid-style storyboard for quick navigation.
- Hover over thumbnails to preview the corresponding shots instantly.

**ShotBoard** offers a seamless way to analyze shot composition, pacing, and transitions in any MP4 video file.

![screenshot](./Example/Screencopy.png)

## Installation

### Install ffmpeg
ffmpeg is required for video processing. Here's how to install it on Windows 10+:

*Windows users will find more visual installation instructions [on this page](https://phoenixnap.com/kb/ffmpeg-windows).*

1. **Download ffmpeg**:
- Go to the [ffmpeg official website](https://ffmpeg.org/download.html).
- Under "**Get packages & executable files**", select a pre-built version for Windows.
- Download the ZIP file for your system (e.g. ffmpeg-release-essentials.zip).

2. **Extract the ffmpeg files**:

- Extract the ZIP file contents to a folder of your choice (e.g. C:\ffmpeg).

3. **Add ffmpeg to the PATH environment variable**:

- Open the <kbd>Start</kbd> menu, search for "**Environment Variables**", and select "**Edit the system environment variables**".
- In the "**System Properties**" window, click <kbd>Environment Variables</kbd>.
- Under **System variables**, find the **PATH** variable and click <kbd>Edit</kbd>.
- Click <kbd>New</kbd>, then enter the `bin` folder path where ffmpeg is located (e.g. C:\ffmpeg\bin).
- Click <kbd>OK</kbd> to save your changes.

4. **Test the installation**:

- Open Command Prompt and type `ffmpeg --version`. If ffmpeg is installed correctly, you’ll see version information displayed.

### Install Python 3.10.6
Ensure **Python 3.10.6** is installed on your system (although it might work on newer versions as well).

1. **Download Python**:

- Go to the [official Python website](https://www.python.org/downloads/).
- Download the installer (e.g. Windows x86-64 executable installer for 64-bit systems).

2. **Install Python**:

- Run the installer and check the box for **Add Python to PATH**.
- Choose **Customize installation** if you want, but the default settings should work fine.
- Complete the installation.

3. **Verify Python installation**:

- Open Command Prompt and type `python --version`. If Python is installed correctly, it will display the version number.

### Install the Necessary Dependencies
1. **Install the required dependencies** (e.g. with **pip**, Python's package manager):

- Open Command Prompt or PowerShell.
- Run the following command to install all dependencies: `pip install PyQt5 opencv-python ffmpeg-python numpy`

If you encounter issues with permissions, try adding `--user` to the command: `pip install --user PyQt5 opencv-python ffmpeg-python numpy`

2. **Verify installation**:

- Run the following command to confirm the dependencies are installed:
`pip show PyQt5 opencv-python ffmpeg-python numpy`

You're now ready to run **ShotBoard**! 🎉

## User Guide: How to Use the ShotBoard Interface

### Opening a Video File
To begin working with a video:
1. Click on the menu **File > Open Video**.
2. Select the desired video file from your system.

### Navigating Through the Video
Use the slider/spinbox or the arrow keys on your keyboard to navigate the video frame-by-frame or jump by larger intervals:
- **Left/Right Arrow**: Move backward or forward by 1 frame.
- **Shift + Left/Right Arrow**: Move backward or forward by 1 second.
- **Ctrl + Left/Right Arrow**: Move backward or forward by 10 seconds.
- **Alt + Left/Right Arrow**: Move backward or forward by 60 seconds.

### Scanning and Detecting Shots
To detect all shots in the video:
1. Click on the menu **Edit > Scan Shots**.
2. ShotBoard will analyze the video and display the detected shots as thumbnails in a grid.

### Saving and Opening Shot Lists
- To save the detected shots for later use: Click on **File > Save** or **File > Save As**.
- To load a previously saved shot list: Click on **File > Open Shot List** and select the file.

### Visualizing Shots
- To preview a shot: Hover the mouse cursor over a thumbnail. The thumbnail will animate and play the shot as long as you hover it.

### Setting the Main Video to the Beginning of a Specific Shot
- To set the main video to the start of a specific shot: Click on the corresponding shot thumbnail.

### Correcting Shot Detection Errors
If the shot detection process misses or incorrectly identifies shots, you can manually adjust them:

#### Adding a New Shot
If a shot contains multiple distinct shots:
1. Use the slider/spinbox to locate the frame where the first undetected shot begins.
2. Click on the button **Insert new shot starting at this frame** to split the incriminated shot at the selected position. A new shot will be added to the list, and the original shot will be shortened accordingly.

#### Merging Shots
If a single shot has been incorrectly subdivided into multiple shots:
1. Click on the first shot thumbnail in the series.
2. **Shift + Click** on the last shot thumbnail in the series to select all relevant shots.
3. Click on the button **Merge selected shots** to combine them into one.
   
## Conclusion  
ShotBoard provides an intuitive and efficient way to analyze movies, study cinematic storytelling and explore editing techniques. 

We hope you enjoy using ShotBoard and find it helpful in your creative or analytical endeavors. If you have any feedback, suggestions, or run into issues, please don’t hesitate to reach out or contribute to the project.  

**Happy storyboarding and exploring cinema!** 🎥✨  
