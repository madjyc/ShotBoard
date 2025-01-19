![License](https://img.shields.io/badge/license-MIT-green.svg)
![Godot Version](https://img.shields.io/badge/Python-3.10.6-orange.svg)
![Version](https://img.shields.io/badge/version-v0.1.0-blue.svg)

# ShotBoard v0.1.0

**ShotBoard: Visualize movies shot by shot**

**ShotBoard** is a Python application designed for **filmmakers**, **editors**, **storyboarders** and **enthusiasts** alike, who want to explore the structural breakdown of a movie.

With **ShotBoard**, you can:
* Open a movie file (MP4) and detect its individual shots automatically.
* Display those shots as thumbnails in a grid-style storyboard for quick navigation.
* Hover over thumbnails to preview the corresponding shots instantly.

**ShotBoard** offers a seamless way to analyze shot composition, pacing, and transitions in any MP4 video file.

![screenshot](./Example/Screencopy.png)

## Installation (/!\WIP/!\)

### Install ffmpeg
FFmpeg is required for video processing. Here's how to install it:
*If need be, Windows users will find clear installation instructions [on this page](https://phoenixnap.com/kb/ffmpeg-windows).*

1. Download ffmpeg:
* Go to the [FFmpeg official website](https://ffmpeg.org/download.html).
* Under "**Get packages & executable files**", select a pre-built version for Windows.
* Download the ZIP file for your system (e.g. ffmpeg-release-essentials.zip).

2. Extract the ffmpeg files:
Extract the ZIP file contents to a folder (e.g. C:\ffmpeg).

3. Add ffmpeg to the **PATH** environment variable:
* Open the <kbd>Start</kbd> menu, search for "**Environment Variables**", and select "**Edit the system environment variables**".
* In the "**System Properties**" window, click <kbd>Environment Variables</kbd>.
* Under **System variables**, find the **Path** variable and click <kbd>Edit</kbd>.
* Click <kbd>New</kbd>, then enter the `bin` folder path where ffmpeg is located (e.g. C:\ffmpeg\bin).
* Click <kbd>OK</kbd> to save your changes.

4. Test the installation:
* Open Command Prompt and type `ffmpeg --version`.
If ffmpeg is installed correctly, you’ll see version information displayed.

### Install Python 3.10.6
Ensure **Python 3.10.6** is installed on your system (although it might work on newer versions as well).

1. Download Python:
* Go to the [official Python website](https://www.python.org/downloads/).
* Download the installer (Windows x86-64 executable installer for 64-bit systems).

2. Install Python:
* Run the installer and check the box for **Add Python to PATH**.
* Choose **Customize installation** if you want, but the default settings should work fine.
* Complete the installation.

3. Verify Python installation:
* Open Command Prompt and type `python --version`.
If Python is installed correctly, it will display the version number.

### Install the Necessary Dependencies
Install the required dependencies (e.g. with **pip**, Python's package manager):

* Open Command Prompt or PowerShell.
* Run the following command to install all dependencies:
`pip install PyQt5 opencv-python ffmpeg-python numpy`

If you encounter issues with permissions, try adding --user to the command:
`pip install --user PyQt5 opencv-python ffmpeg-python numpy`

* Verify installation:
Run the following command to confirm the dependencies are installed:
`pip show PyQt5 opencv-python ffmpeg-python numpy`

You're now ready to run **ShotBoard**! 🎉
