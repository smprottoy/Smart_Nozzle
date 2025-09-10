import re
import mmap
import pandas as pd
import tkinter as tk
from tkinter import filedialog
import os
import sys

class GcodeParser:
    """
    Encapsulates G-code parsing and statistics.
    """
    def __init__(self, file_path=None):
        self.file_path = file_path or self.select_file()
        self.stats_df = None

    @staticmethod
    def select_file():
        """
        Open a dialog rooted in "./Gcodes" to pick a .gcode file.
        """
        script_dir = os.path.dirname(os.path.abspath(__file__))
        initial_dir = os.path.join(script_dir, 'Gcodes')
        root = tk.Tk()
        root.withdraw()
        path = filedialog.askopenfilename(
            title="Select G-code File",
            initialdir=initial_dir,
            filetypes=[("G-code files", "*.gcode"), ("All files", "*.*")]
        )
        if not path:
            sys.exit("No file selected, exiting.")
        return path

    def parse_stats(self):
        """
        Reads the G-code and computes extrusion/travel counts and times.
        """
        cmd_re = re.compile(r'^(G[0-9]+|M[0-9]+)\b')
        mv_re  = re.compile(r'X(-?\d+(\.\d+)?)\s*Y(-?\d+(\.\d+)?)')
        fr_re  = re.compile(r'F(\d+(\.\d+)?)')

        counts = {'Extrusion Commands': 0, 'Travel Commands': 0}
        times  = {'Extrusion Time (s)': 0.0, 'Travel Time (s)': 0.0}
        pos    = {'X': 0.0, 'Y': 0.0}
        feed   = 0.0

        with open(self.file_path, 'r+b') as f:
            mm = mmap.mmap(f.fileno(), 0, access=mmap.ACCESS_READ)
            for raw in iter(mm.readline, b''):
                line = raw.decode('ascii', 'ignore').strip()
                if not line or line.startswith(';'):
                    continue
                m = cmd_re.match(line)
                if not m:
                    continue
                # update feedrate
                fr = fr_re.search(line)
                if fr:
                    feed = float(fr.group(1))
                # determine move
                extrusion = ('E' in line and m.group(1)=='G1')
                mv = mv_re.search(line)
                if mv and feed > 0:
                    x, y = float(mv.group(1)), float(mv.group(3))
                    dx, dy = x-pos['X'], y-pos['Y']
                    dist = (dx*dx + dy*dy)**0.5
                    t = dist/feed*60
                    key = 'Extrusion Time (s)' if extrusion else 'Travel Time (s)'
                    cnt = 'Extrusion Commands' if extrusion else 'Travel Commands'
                    times[key] += t
                    counts[cnt] += 1
                    pos['X'], pos['Y'] = x, y
            mm.close()

        total = times['Extrusion Time (s)'] + times['Travel Time (s)']
        pct_extr = (times['Extrusion Time (s)']/total*100) if total else 0
        self.stats_df = pd.DataFrame({
            'Count':         [counts['Extrusion Commands'], counts['Travel Commands']],
            'Time (s)':      [times['Extrusion Time (s)'], times['Travel Time (s)']],
            'Percentage (%)':[pct_extr, 100-pct_extr]
        }, index=['Extrusion','Travel'])
        return self.stats_df
    
    def split_file(self):
        """
        Splits the G-code file into header, layers (keyed by Z height), and footer.
        Uses Z-move detection. Corrected footer detection logic (more conservative).
        """
        print(f"Attempting to split file: {self.file_path}")
        print("INFO: Using Z-move detection for layer splitting.")
        try:
            with open(self.file_path, 'r', encoding='utf-8') as f:
                lines = [l.rstrip('\n') for l in f]
        except UnicodeDecodeError:
            print("Warning: UTF-8 decoding failed, trying latin-1 encoding.")
            with open(self.file_path, 'r', encoding='latin-1') as f:
                lines = [l.rstrip('\n') for l in f]
        except Exception as e:
            print(f"Error reading G-code file in split_file: {e}")
            return [], {}, []

        header, footer = [], []
        layers = {}
        current_layer_z = None # The Z height defining the current layer being populated
        last_gcode_z = 0.0   # The Z value found in the most recent G0/G1 command with Z
        found_first_layer_move = False # Flag becomes true after first significant Z move
        min_layer_z = 0.1 # Ignore Z moves below this threshold as potential first layer starts

        # --- Regex to find G0/G1 commands and extract Z ----
        g01_command_re = re.compile(r'^(G[01])\b(.*)')
        z_param_re = re.compile(r'\bZ(-?\d+(?:\.\d+)?)')

        # --- Definitive Footer Command Markers ---
        # Only commands that strongly indicate the print END sequence
        definitive_footer_markers = ['M104 S0', 'M140 S0', 'M84', 'Turn off steppers']

        print(f"Processing {len(lines)} lines using Z-move layer detection...")

        # --- Main Processing Loop (NO footer detection inside) ---
        # This loop assigns lines to header or layers dictionary first
        for i, line in enumerate(lines):
            # --- Process G0/G1 for Z changes ---
            new_z_this_line = None
            g_match = g01_command_re.match(line)
            if g_match:
                params = g_match.group(2)
                z_match = z_param_re.search(params)
                if z_match:
                    try:
                        new_z_this_line = float(z_match.group(1))
                        # --- Layer Change Logic ---
                        if new_z_this_line > (last_gcode_z + 0.001) and new_z_this_line >= min_layer_z:
                            if current_layer_z is None or abs(new_z_this_line - current_layer_z) > 0.001:
                                current_layer_z = new_z_this_line
                                if current_layer_z not in layers:
                                    layers[current_layer_z] = []
                                    print(f"  Detected new layer start via Z-move: Z = {current_layer_z:.3f} at line {i+1}")
                                if not found_first_layer_move:
                                    found_first_layer_move = True
                        # Update last_gcode_z only if Z was successfully parsed
                        last_gcode_z = new_z_this_line
                    except ValueError:
                        pass # Ignore if Z value is not parsable

            # --- Line Assignment ---
            if not found_first_layer_move:
                header.append(line)
            elif current_layer_z is not None:
                # Add line temporarily to the current layer dictionary
                # Footer separation happens *after* this loop
                layers[current_layer_z].append(line)

        # --- Post-Loop Footer Separation ---
        potential_footer_start_layer_z = None
        potential_footer_start_line_index_in_layer = -1
        found_definitive_footer_marker = False

        # Check only the last few layers or last N lines for efficiency
        sorted_layer_keys = sorted(layers.keys(), reverse=True)
        lines_to_check_for_footer = 200 # Look within the last ~200 lines added to layers

        checked_line_count = 0
        for layer_z in sorted_layer_keys:
            if checked_line_count >= lines_to_check_for_footer:
                break # Stop checking if we've looked back far enough

            layer_lines = layers[layer_z]
            start_index_to_check = max(0, len(layer_lines) - (lines_to_check_for_footer - checked_line_count))

            # Iterate backwards from end of layer, but only within the check window
            for idx_in_layer in range(len(layer_lines) - 1, start_index_to_check - 1, -1):
                line = layer_lines[idx_in_layer]
                checked_line_count += 1

                is_definitive_footer = any(marker in line for marker in definitive_footer_markers)

                if is_definitive_footer:
                    print(f"  Definitive footer marker found in layer {layer_z} at index {idx_in_layer}: {line}")
                    # Store the Z and index where the *first* definitive marker was seen (from the end)
                    potential_footer_start_layer_z = layer_z
                    potential_footer_start_line_index_in_layer = idx_in_layer
                    found_definitive_footer_marker = True
                    # Don't break immediately, let it find the earliest marker within the window if multiple exist

            # If we found a marker in this layer, we don't need to check earlier layers further back
            # (unless the marker was extremely early in this layer, edge case ignored for now)
            # if found_definitive_footer_marker:
            #      break

        # --- Move footer lines from layers to footer list ---
        if found_definitive_footer_marker and potential_footer_start_layer_z is not None:
            print(f"Moving lines from index {potential_footer_start_line_index_in_layer} in layer {potential_footer_start_layer_z} onwards to footer.")
            layer_to_split = layers[potential_footer_start_layer_z]
            footer = layer_to_split[potential_footer_start_line_index_in_layer:]
            layers[potential_footer_start_layer_z] = layer_to_split[:potential_footer_start_line_index_in_layer]

            # Move lines from any subsequent layers entirely to footer
            # (This shouldn't happen if footer is detected correctly in the last layer it appears)
            keys_to_delete = []
            for layer_z in sorted(layers.keys()):
                if layer_z > potential_footer_start_layer_z:
                    print(f"Moving all lines from subsequent layer {layer_z} to footer.")
                    footer.extend(layers[layer_z])
                    keys_to_delete.append(layer_z)
            for key in keys_to_delete:
                del layers[key]
        else:
            print("Warning: No definitive footer markers found near end of file. Footer might be empty or included in last layer.")
            footer = [] # Assume no separate footer if markers aren't found


        # --- Final Checks ---
        if not layers: print("ERROR: No layers were identified using Z-move detection.")

        print(f"Split complete (Z-move): Header={len(header)} lines, Layers={len(layers)}, Footer={len(footer)} lines")
        total_lines_processed = len(header) + sum(len(l) for l in layers.values()) + len(footer)
        print(f"Total lines processed: {total_lines_processed} / {len(lines)}")
        if total_lines_processed != len(lines): print("WARNING: Line count mismatch after splitting!")

        return header, layers, footer
    # --- End of replacement for split_file ---

    def get_header(self):
        return self.split_file()[0]

    def get_layers(self):
        return self.split_file()[1]

    def get_footer(self):
        return self.split_file()[2]

def main():
    parser = GcodeParser()
    df = parser.parse_stats()
    print(f"Parsed: {parser.file_path}\n")
    print(df)

if __name__ == "__main__":
    main()