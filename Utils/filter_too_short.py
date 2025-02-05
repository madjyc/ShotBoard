import json
import os
from PyQt5.QtWidgets import QApplication, QFileDialog

# Initialize a QApplication instance (needed for QFileDialog)
app = QApplication([])

# Open a file dialog to let the user select a JSON file
file_path, _ = QFileDialog.getOpenFileName(None, "Select JSON File", "", "JSON Files (*.json);;All Files (*)")

# Proceed only if the user selected a file
if file_path:
    # Load the JSON file
    with open(file_path, "r") as file:
        data = json.load(file)

    frame_count = data["frame_count"]
    shots = data["shots"]

    # Filter out shots that are too close to each other
    filtered_shots = [shots[0]]
    for i in range(1, len(shots)):
        if shots[i] - shots[i - 1] >= 3:
            filtered_shots.append(shots[i])

    # Save the original file with "_original" suffix
    original_file = os.path.splitext(file_path)[0] + "_original.json"
    os.rename(file_path, original_file)

    # Save the filtered output with the original filename
    data["shots"] = filtered_shots
    with open(file_path, "w") as file:
        json.dump(data, file)

    print(f"Original file renamed to: {original_file}")
    print(f"Filtered shot list saved to: {file_path}")
