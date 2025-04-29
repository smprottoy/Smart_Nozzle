import re
import mmap
import pandas as pd
import sys
import os
import tkinter as tk
from tkinter import filedialog

def parse_gcode(file_path):
    """
    Parses the G-code file at file_path and returns a DataFrame
    with counts and timing of extrusion vs travel commands.
    """
    command_pattern = re.compile(r'^(G[0-9]+|M[0-9]+)\b')
    movement_pattern = re.compile(r'X(-?\d+(\.\d+)?)\s*Y(-?\d+(\.\d+)?)')
    feedrate_pattern = re.compile(r'F(\d+(\.\d+)?)')

    counts = {'Extrusion Commands': 0, 'Travel Commands': 0}
    times = {'Extrusion Time (s)': 0.0, 'Travel Time (s)': 0.0}
    current_pos = {'X': 0.0, 'Y': 0.0}
    current_feedrate = 0.0

    with open(file_path, 'r+b') as f:
        mm = mmap.mmap(f.fileno(), 0, access=mmap.ACCESS_READ)
        for raw in iter(mm.readline, b''):
            line = raw.decode('ascii', 'ignore').strip()
            if not line or line.startswith(';'):
                continue

            m = command_pattern.match(line)
            if not m:
                continue

            fr = feedrate_pattern.search(line)
            if fr:
                current_feedrate = float(fr.group(1))

            is_extrusion = 'E' in line and m.group(1) == 'G1'
            mv = movement_pattern.search(line)
            if mv and current_feedrate > 0:
                x, y = float(mv.group(1)), float(mv.group(3))
                dx, dy = x - current_pos['X'], y - current_pos['Y']
                dist = (dx**2 + dy**2)**0.5
                time_s = (dist / current_feedrate) * 60
                if is_extrusion:
                    counts['Extrusion Commands'] += 1
                    times['Extrusion Time (s)'] += time_s
                else:
                    counts['Travel Commands'] += 1
                    times['Travel Time (s)'] += time_s
                current_pos['X'], current_pos['Y'] = x, y
        mm.close()

    total_time = times['Extrusion Time (s)'] + times['Travel Time (s)']
    extr_pct = (times['Extrusion Time (s)'] / total_time * 100) if total_time else 0

    df = pd.DataFrame({
        'Count': [counts['Extrusion Commands'], counts['Travel Commands']],
        'Time (s)': [times['Extrusion Time (s)'], times['Travel Time (s)']],
        'Percentage (%)': [extr_pct, 100 - extr_pct]
    }, index=['Extrusion', 'Travel'])
    return df

def main():
    # Launch file dialog rooted at "./Gcodes"
    script_dir = os.path.dirname(os.path.abspath(__file__))
    initial_dir = os.path.join(script_dir, 'Gcodes')
    root = tk.Tk()
    root.withdraw()
    file_path = filedialog.askopenfilename(
        title="Select G-code File",
        initialdir=initial_dir,
        filetypes=[("G-code files", "*.gcode"), ("All files", "*.*")]
    )
    if not file_path:
        print("No file selected, exiting.")
        sys.exit(1)

    print(f"Parsing: {file_path}")
    df = parse_gcode(file_path)
    print(df)

if __name__ == '__main__':
    main()