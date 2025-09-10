# smart_nozzle.py
import re
import math
import os
import sys
import time

# --- Attempt to import the GcodeParser ---
try:
    from gcode_parser import GcodeParser
except ImportError:
    print("ERROR: Could not import GcodeParser from gcode_parser.py.")
    print("       Ensure gcode_parser.py is in the same directory, is error-free,")
    print("       and defines the class 'GcodeParser'.")
    sys.exit(1)
except Exception as e:
    print(f"ERROR: An unexpected error occurred during GcodeParser import: {e}")
    sys.exit(1)


# --- Regular Expressions (Defined at Module Level) ---
G0G1_RE = re.compile(r'^(G[01])\b(.*)')
PARAMS_RE = re.compile(r'([XYZEF])(-?\d+(\.\d+)?)')
EXTRUDE_RE = re.compile(r'\bE(-?\d+(\.\d+)?)(?!\d)')
FEEDRATE_RE = re.compile(r'\bF(\d+(\.\d+)?)')
Z_PARAM_RE = re.compile(r'\bZ(-?\d+(\.\d+)?)')


# --- Optimizer Class Definition ---
class GcodeOptimizer:
    """
    Optimizes G-code by reordering extrusion paths within layers
    using 2-Opt TSP heuristic to minimize non-printing travel distance.
    """

    def __init__(self, file_path=None):
        """ Initializes the optimizer, finds global max feedrate from G0/G1. """
        print("Initializing G-code Optimizer...")
        try:
             self.parser = GcodeParser(file_path)
        except NameError:
            print("FATAL ERROR: GcodeParser class definition not found."); sys.exit(1)
        except Exception as e:
             print(f"Error initializing GcodeParser: {e}"); sys.exit(1)

        if not hasattr(self.parser, 'file_path') or not self.parser.file_path:
            print("No file path available from GcodeParser. Exiting."); sys.exit(0)

        print(f"Selected file: {self.parser.file_path}")
        self.optimized_gcode_lines = []
        self.header = []
        self.layers = {}
        self.footer = []

        # --- Find and store global max feedrate from ANY G0/G1 line ---
        self.global_max_feedrate = 3000.0 # Default fallback if absolutely none found
        self._find_global_max_g0_g1_feedrate()


    def _extract_params(self, line):
        # ... (keep implementation) ...
        params = {}
        for match in PARAMS_RE.finditer(line):
            try: params[match.group(1)] = float(match.group(2))
            except (ValueError, IndexError): pass
        return params


    def _calculate_distance(self, pos1, pos2):
        # ... (keep implementation) ...
        if pos1 is None or pos2 is None: return 0.0
        x1 = pos1.get('X', 0.0); y1 = pos1.get('Y', 0.0)
        x2 = pos2.get('X', 0.0); y2 = pos2.get('Y', 0.0)
        if x1 is None or y1 is None or x2 is None or y2 is None: return 0.0
        try:
             dx = float(x1) - float(x2); dy = float(y1) - float(y2)
             return math.sqrt(dx*dx + dy*dy)
        except (TypeError, ValueError): return 0.0


    def _find_global_max_g0_g1_feedrate(self):
        """
        MODIFIED: Scans the entire input file once to find the maximum feedrate
        specified on ANY G0 or G1 line.
        """
        print("Scanning file for maximum G0/G1 feedrate...")
        max_feed = 0.0
        found_f = False
        if not hasattr(self, 'parser') or not hasattr(self.parser, 'file_path') or not self.parser.file_path or not os.path.exists(self.parser.file_path):
             print("Warning: Cannot scan for feedrate, input file path invalid."); return

        try:
             with open(self.parser.file_path, 'r', encoding='utf-8', errors='ignore') as f:
                  for line in f:
                       g_match = G0G1_RE.match(line)
                       # Check BOTH G0 and G1 lines
                       if g_match:
                            f_match = FEEDRATE_RE.search(g_match.group(2))
                            if f_match:
                                 try:
                                     feed = float(f_match.group(1))
                                     found_f = True
                                     if feed > max_feed:
                                         max_feed = feed
                                 except ValueError: pass # Ignore non-numeric F
        except Exception as e: print(f"Warning: Error reading file during feedrate scan: {e}")

        if found_f and max_feed > 0:
             print(f"Found overall maximum G0/G1 feedrate: {max_feed:.2f}")
             self.global_max_feedrate = max_feed
        else:
             print(f"Could not find any F value on G0/G1 lines. Using default: {self.global_max_feedrate:.2f}")


    def identify_segments(self, layer_z, layer_lines):
        """ Identifies extrusion segments based on extrusion state and travel/retraction. """
        # ... (Keep the latest refined version of this method) ...
        segments = []
        current_segment_lines = []
        pos_before_segment = {'X': None, 'Y': None, 'Z': layer_z}
        current_abs_pos = {'X': None, 'Y': None, 'Z': layer_z}
        was_extruding = False
        for line_index, line in enumerate(layer_lines):
            g_match = G0G1_RE.match(line)
            params = self._extract_params(line)
            if 'X' in params: current_abs_pos['X'] = params['X']
            if 'Y' in params: current_abs_pos['Y'] = params['Y']
            if 'Z' in params: current_abs_pos['Z'] = params['Z']
            e_match = EXTRUDE_RE.search(line)
            e_value_from_re = float(e_match.group(1)) if e_match else None
            is_extruding_cmd = g_match and g_match.group(1) == 'G1' and e_value_from_re is not None and e_value_from_re > 1e-5
            is_retracting_cmd = g_match and g_match.group(1) == 'G1' and e_value_from_re is not None and e_value_from_re < -1e-5
            is_travel_cmd = (g_match and g_match.group(1) == 'G0') or \
                            (g_match and g_match.group(1) == 'G1' and e_value_from_re is None and ('X' in params or 'Y' in params))
            should_end_segment = False
            if was_extruding and (is_travel_cmd or is_retracting_cmd): should_end_segment = True
            if should_end_segment:
                if current_segment_lines:
                    # --- Finalization Logic ---
                    start_pos_context=dict(pos_before_segment); seg_start_pos={'X':None,'Y':None}; seg_end_pos={'X':None,'Y':None}
                    segment_abs_pos_tracker={'X':start_pos_context.get('X'),'Y':start_pos_context.get('Y')}
                    first_move_params=None; has_moves=False; contains_extrusion=False
                    for seg_line in current_segment_lines:
                         line_g_match=G0G1_RE.match(seg_line)
                         if line_g_match:
                             line_params=self._extract_params(seg_line)
                             line_e_match=EXTRUDE_RE.search(seg_line); line_e_val=float(line_e_match.group(1)) if line_e_match else None
                             if line_e_val is not None and line_e_val > 1e-5: contains_extrusion=True
                             is_line_xy_move = 'X' in line_params or 'Y' in line_params
                             if is_line_xy_move: has_moves=True
                             if 'X' in line_params: segment_abs_pos_tracker['X']=line_params['X']
                             if 'Y' in line_params: segment_abs_pos_tracker['Y']=line_params['Y']
                             if first_move_params is None and is_line_xy_move:
                                 first_move_params=line_params
                                 seg_start_pos['X']=first_move_params.get('X',start_pos_context.get('X'))
                                 seg_start_pos['Y']=first_move_params.get('Y',start_pos_context.get('Y'))
                                 if seg_start_pos['X'] is None: seg_start_pos['X']=0.0
                                 if seg_start_pos['Y'] is None: seg_start_pos['Y']=0.0
                    seg_end_pos['X']=segment_abs_pos_tracker['X']; seg_end_pos['Y']=segment_abs_pos_tracker['Y']
                    if not has_moves:
                         seg_start_pos=dict(start_pos_context); seg_end_pos=dict(start_pos_context)
                         if seg_start_pos['X'] is None: seg_start_pos['X']=0.0
                         if seg_start_pos['Y'] is None: seg_start_pos['Y']=0.0
                         if seg_end_pos['X'] is None: seg_end_pos['X']=0.0
                         if seg_end_pos['Y'] is None: seg_end_pos['Y']=0.0
                    # Validation
                    if seg_start_pos.get('X') is not None and seg_start_pos.get('Y') is not None and \
                       seg_end_pos.get('X') is not None and seg_end_pos.get('Y') is not None:
                         start_eq_end = abs(seg_start_pos['X'] - seg_end_pos['X']) < 0.001 and abs(seg_start_pos['Y'] - seg_end_pos['Y']) < 0.001
                         if not (start_eq_end and not contains_extrusion):
                              segments.append({'lines': list(current_segment_lines),'start_pos': dict(seg_start_pos),'end_pos': dict(seg_end_pos)})
                         # else: Debug print skipping
                    # else: print(f"ERROR: Skipping segment (Layer {layer_z:.3f}) due to None start/end XY.")
                current_segment_lines = []
                pos_before_segment = dict(current_abs_pos)
            current_segment_lines.append(line)
            if is_extruding_cmd: was_extruding = True
            elif is_travel_cmd or is_retracting_cmd: was_extruding = False
        # --- Add Last Segment ---
        if current_segment_lines and was_extruding:
             # --- Finalization Logic ---
             start_pos_context=dict(pos_before_segment)
             seg_start_pos = {'X': None, 'Y': None}; seg_end_pos = {'X': None, 'Y': None}
             segment_abs_pos_tracker = {'X': start_pos_context.get('X'), 'Y': start_pos_context.get('Y')}
             first_move_params = None; has_moves = False; contains_extrusion = False
             for seg_line in current_segment_lines:
                  line_g_match = G0G1_RE.match(seg_line)
                  if line_g_match:
                      line_params = self._extract_params(seg_line)
                      line_e_match = EXTRUDE_RE.search(seg_line); line_e_val = float(line_e_match.group(1)) if line_e_match else None
                      if line_e_val is not None and line_e_val > 1e-5: contains_extrusion = True
                      is_line_xy_move = 'X' in line_params or 'Y' in line_params
                      if is_line_xy_move: has_moves = True
                      if 'X' in line_params: segment_abs_pos_tracker['X'] = line_params['X']
                      if 'Y' in line_params: segment_abs_pos_tracker['Y'] = line_params['Y']
                      if first_move_params is None and is_line_xy_move:
                          first_move_params = line_params
                          seg_start_pos['X'] = first_move_params.get('X', start_pos_context.get('X'))
                          seg_start_pos['Y'] = first_move_params.get('Y', start_pos_context.get('Y'))
                          if seg_start_pos['X'] is None: seg_start_pos['X'] = 0.0
                          if seg_start_pos['Y'] is None: seg_start_pos['Y'] = 0.0
             seg_end_pos['X'] = segment_abs_pos_tracker['X']; seg_end_pos['Y'] = segment_abs_pos_tracker['Y']
             if not has_moves:
                 seg_start_pos = dict(start_pos_context); seg_end_pos = dict(start_pos_context)
                 if seg_start_pos['X'] is None: seg_start_pos['X'] = 0.0
                 if seg_start_pos['Y'] is None: seg_start_pos['Y'] = 0.0
                 if seg_end_pos['X'] is None: seg_end_pos['X'] = 0.0
                 if seg_end_pos['Y'] is None: seg_end_pos['Y'] = 0.0
             # Validation
             if seg_start_pos.get('X') is not None and seg_start_pos.get('Y') is not None and \
                seg_end_pos.get('X') is not None and seg_end_pos.get('Y') is not None:
                  start_eq_end = abs(seg_start_pos['X'] - seg_end_pos['X']) < 0.001 and abs(seg_start_pos['Y'] - seg_end_pos['Y']) < 0.001
                  if not (start_eq_end and not contains_extrusion):
                       segments.append({'lines': list(current_segment_lines),'start_pos': dict(seg_start_pos),'end_pos': dict(seg_end_pos)})
                  # else: Debug print skipping
             # else: print(f"ERROR: Skipping LAST segment (Layer {layer_z:.3f}) due to None start/end XY.")
             # --- End Finalization ---

        return segments


    def optimize_layer(self, layer_z, layer_lines):
        """ Optimizes travel moves using 2-Opt TSP and global max G0/G1 feedrate. """
        segments = self.identify_segments(layer_z, layer_lines)
        if not segments or len(segments) <= 1: return layer_lines
        num_segments = len(segments)

        # --- Use the globally stored MAX G0/G1 feedrate for travel ---
        travel_feedrate = self.global_max_feedrate
        # ---

        # --- Helper function for path distance ---
        def calculate_path_distance(order, segments):
            # ... (Keep implementation with safety checks) ...
            total_dist = 0.0
            for k in range(num_segments - 1):
                idx1, idx2 = order[k], order[k+1]
                if not (0 <= idx1 < len(segments) and 0 <= idx2 < len(segments)): return float('inf')
                pos1, pos2 = segments[idx1].get('end_pos'), segments[idx2].get('start_pos')
                if pos1 is None or pos2 is None or \
                   pos1.get('X') is None or pos1.get('Y') is None or \
                   pos2.get('X') is None or pos2.get('Y') is None: return float('inf')
                total_dist += self._calculate_distance(pos1, pos2)
            return total_dist

        # --- 2-Opt Algorithm ---
        current_order = list(range(num_segments))
        initial_distance = calculate_path_distance(current_order, segments)
        if initial_distance == float('inf'): return layer_lines

        best_order = current_order[:]
        best_distance = initial_distance

        improved = True; iteration_count = 0; max_iterations = max(500, num_segments * 10)
        while improved and iteration_count < max_iterations:
            iteration_count += 1; improved = False
            for i in range(num_segments - 1):
                for j in range(i + 1, num_segments):
                    new_order = best_order[:]; sub_path = new_order[i+1 : j+1]
                    sub_path.reverse(); new_order[i+1 : j+1] = sub_path
                    new_distance = calculate_path_distance(new_order, segments)
                    if new_distance < (best_distance - 1e-3):
                        best_order = new_order; best_distance = new_distance; improved = True
                        break # Use first improvement strategy
                if improved: break
        # --- End 2-Opt ---

        # --- Reconstruct G-code ---
        optimized_gcode = []
        if not best_order: return layer_lines

        first_segment_index = best_order[0]
        if not (0 <= first_segment_index < len(segments)): return layer_lines
        optimized_gcode.extend(segments[first_segment_index]['lines'])
        last_pos = segments[first_segment_index].get('end_pos')
        if last_pos is None or last_pos.get('X') is None or last_pos.get('Y') is None: return layer_lines

        for k in range(1, num_segments):
            current_segment_index = best_order[k]
            if not (0 <= current_segment_index < len(segments)): break
            next_seg = segments[current_segment_index]
            start_pos = next_seg.get('start_pos')
            if start_pos is None or start_pos.get('X') is None or start_pos.get('Y') is None: continue

            start_x, start_y = start_pos['X'], start_pos['Y']
            if self._calculate_distance(last_pos, start_pos) > 1e-3:
                 # --- Use the determined max G0/G1 feedrate for generated G0 ---
                 travel_move = f"G0 F{travel_feedrate:.2f} X{start_x:.4f} Y{start_y:.4f}"
                 optimized_gcode.append(travel_move)

            optimized_gcode.extend(next_seg['lines'])
            current_end_pos = next_seg.get('end_pos')
            if current_end_pos is not None and current_end_pos.get('X') is not None and current_end_pos.get('Y') is not None:
                 last_pos = current_end_pos

        return optimized_gcode

    def optimize_gcode(self):
        """ Parses G-code, optimizes layers, reconstructs file content. """
        start_time = time.time()
        print("Starting G-code optimization process...")
        try:
            self.header, self.layers, self.footer = self.parser.split_file()
            if not isinstance(self.layers, dict) or not self.layers:
                print("Error: GcodeParser did not return valid layers dictionary."); return False
            print(f"Found {len(self.layers)} layers.")
        except AttributeError: print("Error: Problem calling GcodeParser's split_file method."); return False
        except Exception as e: print(f"Error during parsing/splitting: {e}"); return False

        optimized_layers_content = []
        try: sorted_layer_keys = sorted(self.layers.keys())
        except TypeError: print("Error: Layer keys are not sortable."); return False

        layer_count_processed = 0
        layer_count_optimized = 0 # Count layers where 2-opt actually ran
        for layer_z in sorted_layer_keys:
            layer_lines = self.layers.get(layer_z, [])
            if not layer_lines: continue
            layer_count_processed += 1
            optimized_layers_content.append(f"; OPTIMIZED_LAYER_START Z={layer_z:.3f}")
            optimized_lines = self.optimize_layer(layer_z, layer_lines)
            # Check if optimization actually ran (more than 1 segment existed)
            if len(optimized_lines) != len(layer_lines): # Crude check: Did lines change?
                 layer_count_optimized +=1
            optimized_layers_content.extend(optimized_lines)

        self.optimized_gcode_lines = self.header + optimized_layers_content + self.footer
        end_time = time.time()
        print(f"Layer optimization loop finished processing {layer_count_processed} layers in {end_time - start_time:.2f} seconds.")
        if layer_count_optimized > 0:
             print(f"Optimization potentially applied to {layer_count_optimized} layers (those with >1 segment).")
        else:
             print("No layers had multiple segments suitable for optimization.")

        return True


    def save_optimized_gcode(self):
        """ Saves the optimized G-code automatically to 'Optimized_Gcodes' sibling folder. """
        # ... (Keep the latest automatic save version without dialog) ...
        if not self.optimized_gcode_lines: print("No optimized G-code generated to save."); return
        if not hasattr(self, 'parser') or not hasattr(self.parser, 'file_path') or not self.parser.file_path:
             print("Error: Cannot determine input file path for saving."); return

        try:
            input_file_path = self.parser.file_path; input_dir = os.path.dirname(input_file_path)
            parent_dir = os.path.dirname(input_dir); output_dir = os.path.join(parent_dir, "Optimized_Gcodes")
            original_filename = os.path.basename(input_file_path)
            optimized_filename = os.path.splitext(original_filename)[0] + "_optimized.gcode"
            output_file_path = os.path.join(output_dir, optimized_filename)

            if not os.path.exists(output_dir):
                try: print(f"Creating output directory: {output_dir}"); os.makedirs(output_dir)
                except OSError as e: print(f"Error creating output directory {output_dir}: {e}"); print(f"Fallback: Saving to parent directory: {parent_dir}"); output_file_path = os.path.join(parent_dir, optimized_filename)
                except Exception as e_inner: print(f"Unexpected error creating directory {output_dir}: {e_inner}"); print(f"Fallback: Saving to parent directory: {parent_dir}"); output_file_path = os.path.join(parent_dir, optimized_filename)

            print(f"Attempting to automatically save optimized file to: {output_file_path}")
            with open(output_file_path, 'w', encoding='utf-8') as f: f.write("\n".join(self.optimized_gcode_lines))
            print(f"Optimized G-code saved successfully.")

        except AttributeError: print("Error: Missing parser information needed for saving.")
        except Exception as e: print(f"Error during automatic save operation: {e}"); import traceback; traceback.print_exc()


# --- Main Function (Defined at Module Level) ---
def main():
    """ Main execution function: Creates optimizer and runs the process. """
    try:
        optimizer = GcodeOptimizer()
        if optimizer.optimize_gcode():
            optimizer.save_optimized_gcode()
        else:
            print("\nOptimization process failed or aborted.")
    except ValueError as ve:
         print(f"Initialization failed: {ve}")
    except NameError as ne:
         print(f"NameError: {ne}. Is GcodeOptimizer class defined correctly?")
    except Exception as e:
        print(f"\nAn unexpected error occurred in main execution: {e}")
        import traceback
        traceback.print_exc()


# --- Script Execution Guard (Module Level) ---
if __name__ == "__main__":
    # This ensures main() runs only when script is executed directly
    main()