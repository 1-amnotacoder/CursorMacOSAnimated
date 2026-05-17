#!/usr/bin/env python3
"""
Mousecape Cursor Pack Processor - PySimpleGUI Edition
Processes cursor animation frames and generates Mousecape-compatible packs

This updated version fixes a Tk/Tcl threading issue by ensuring all GUI updates
are performed on the main thread. Worker threads post events to the GUI thread
using `window.write_event_value` which is thread-safe.

It also now searches the extracted ZIP recursively for directories that contain
PNG files, so ZIPs that wrap folders (or include __MACOSX) are handled.

This version also generates a .cape package (ZIP with .cape extension) that
contains the stacked PNGs and a manifest (JSON). Note: Mousecape's internal
format is proprietary; this .cape is a reasonable manifest-based package that
many users find helpful. If the Mousecape app requires a different internal
format, the manifest here can be adapted.

New features in this commit:
- Configurable target frames per stacked image (default 23)
- Padding modes: repeat first (default), repeat last, mirror
- Preview pane showing the stacked image (updates during processing)
- When padding, filler frames are generated from existing frames (by default the first frame)
- Stack images are square: each frame is resized to (frame_width, frame_width) so final height = frame_width * target_frames
"""

import os
import sys
import zipfile
import shutil
import threading
import json
from pathlib import Path
import PySimpleGUI as sg
from PIL import Image
from datetime import datetime

# Configure PySimpleGUI theme
sg.theme('DarkBlue2')
sg.set_options(font=('Arial', 10), button_element_size=(12, 1))

class CursorProcessor:
    """Handles cursor pack processing logic"""
    
    def __init__(self):
        self.current_file = None
        self.processing = False
        self.window = None
        
    def natural_sort_key(self, path):
        """Sort paths with natural number ordering (frame_1, frame_2, ..., frame_10)"""
        import re
        filename = Path(path).stem
        return [int(text) if text.isdigit() else text.lower() 
                for text in re.split(r'(\d+)', filename)]
    
    def stack_frames(self, folder_path, output_dir, target_frames=23, padding_mode='repeat_first', scale=1):
        """Stack PNG frames vertically and return metadata.

        Frames are resized to square (frame_width x frame_width) where frame_width is
        the width of the first image in the folder. Final stacked image size is
        (frame_width, frame_width * target_frames).

        Returns: (output_path, frame_count, frame_width, frame_height, error)
        """
        try:
            # Get PNG files, ignore AppleDouble files (._*)
            png_files = sorted([p for p in Path(folder_path).glob('*.png') if not p.name.startswith('._')], key=self.natural_sort_key)
            
            if not png_files:
                return None, 0, 0, 0, f"No PNG files found in {folder_path}"
            
            # Determine base width from first image
            first_img = Image.open(png_files[0])
            base_width = first_img.size[0]
            frame_size = int(base_width)  # square frames: width x width
            
            # Build sequence of frames according to target_frames and padding_mode
            n = len(png_files)
            frames_seq = [p for p in png_files]

            if n >= target_frames:
                frames_seq = frames_seq[:target_frames]
            else:
                needed = target_frames - n
                if padding_mode == 'repeat_first':
                    fillers = [png_files[0]] * needed
                    frames_seq.extend(fillers)
                elif padding_mode == 'repeat_last':
                    fillers = [png_files[-1]] * needed
                    frames_seq.extend(fillers)
                elif padding_mode == 'mirror':
                    # mirror the sequence (excluding last to avoid immediate duplicate)
                    mirror = list(reversed(png_files))
                    # append mirror frames repeatedly until we reach target
                    i = 0
                    while len(frames_seq) < target_frames:
                        frames_seq.append(mirror[i % len(mirror)])
                        i += 1
                else:
                    # fallback to repeat_first
                    fillers = [png_files[0]] * needed
                    frames_seq.extend(fillers)

            # Create stacked image: width x (width * target_frames)
            stacked_height = frame_size * target_frames
            stacked = Image.new('RGBA', (frame_size, stacked_height), (0, 0, 0, 0))
            
            for i, png_file in enumerate(frames_seq):
                frame = Image.open(png_file).convert('RGBA')
                # Resize each frame to square frame_size x frame_size
                if frame.size != (frame_size, frame_size):
                    frame = frame.resize((frame_size, frame_size), Image.Resampling.LANCZOS)
                stacked.paste(frame, (0, i * frame_size), frame)
            
            # Save stacked image
            cursor_name = Path(folder_path).name
            output_path = Path(output_dir) / f"{cursor_name}_stacked.png"
            stacked.save(output_path, 'PNG')
            
            return output_path, target_frames, frame_size, frame_size, None
            
        except Exception as e:
            return None, 0, 0, 0, f"Error processing {folder_path}: {str(e)}"
    
    def create_cape(self, final_output: Path, base_name: str, cursor_entries: list, fps: int):
        """Create a .cape package (ZIP with .cape extension) containing stacked PNGs and a manifest."""
        try:
            cape_path = final_output / f"{base_name}.cape"
            # Build manifest
            manifest = {
                'name': base_name,
                'created_at': datetime.utcnow().isoformat() + 'Z',
                'fps': int(fps),
                'cursors': []
            }
            for e in cursor_entries:
                # e: dict with keys name, filename, frames, width, height, hotspot
                manifest['cursors'].append({
                    'name': e['name'],
                    'file': e['filename'],
                    'frames': int(e['frames']),
                    'frame_width': int(e['width']),
                    'frame_height': int(e['height']),
                    'hotspot': e.get('hotspot', {'x': e['width']//2, 'y': e['height']//2})
                })
            
            # Write zip (cape) file
            with zipfile.ZipFile(cape_path, 'w', compression=zipfile.ZIP_DEFLATED) as zf:
                # Add stacked PNGs
                for e in cursor_entries:
                    src = Path(final_output) / e['filename']
                    if src.exists():
                        zf.write(src, arcname=e['filename'])
                # Add manifest.json
                zf.writestr('manifest.json', json.dumps(manifest, indent=2))
                # Also add a copy under Mousecape expected dump filename (JSON form)
                zf.writestr('com.alexzielenski.mousecape.dump.capeux', json.dumps(manifest))
            return cape_path, None
        except Exception as e:
            return None, str(e)
    
    def process_zip(self, zip_path, output_dir, fps=24, scale=1, progress_callback=None, target_frames=23, padding_mode='repeat_first'):
        """Process ZIP file containing cursor packs"""
        try:
            # Create temp extraction directory
            temp_dir = Path(output_dir) / '.temp_extract'
            if temp_dir.exists():
                shutil.rmtree(temp_dir)
            temp_dir.mkdir(parents=True, exist_ok=True)
            
            # Extract ZIP
            with zipfile.ZipFile(zip_path, 'r') as zip_ref:
                zip_ref.extractall(temp_dir)
            
            # Find all directories that contain PNG files (recursively), ignore macOS metadata
            cursor_folders = []
            # Check if PNGs are directly at the root of the extracted temp_dir
            try:
                if any(p.suffix.lower() == '.png' and not p.name.startswith('._') for p in temp_dir.iterdir() if p.is_file()):
                    cursor_folders.append(temp_dir)
            except PermissionError:
                pass
            
            for d in temp_dir.rglob('*'):
                if not d.is_dir():
                    continue
                # Skip any directory that contains a __MACOSX component anywhere in its path
                if any(part == '__MACOSX' for part in d.parts):
                    continue
                # skip hidden dirs
                if d.name.startswith('.'):
                    continue
                try:
                    if any(p.suffix.lower() == '.png' and not p.name.startswith('._') for p in d.iterdir() if p.is_file()):
                        cursor_folders.append(d)
                except PermissionError:
                    continue
            
            # Remove duplicates while preserving order and sort for deterministic output
            seen = set()
            ordered = []
            for p in cursor_folders:
                s = str(p.resolve())
                if s not in seen:
                    seen.add(s)
                    ordered.append(p)
            cursor_folders = sorted(ordered, key=lambda p: str(p))
            total_cursors = len(cursor_folders)
            
            if not cursor_folders:
                # Cleanup temp directory
                shutil.rmtree(temp_dir)
                return None, f"No cursor folders containing PNGs were found in the ZIP"
            
            results = []
            cursor_entries = []
            for idx, cursor_folder in enumerate(cursor_folders):
                cursor_name = cursor_folder.name
                percent = int(((idx) / max(1, total_cursors)) * 100)
                if progress_callback:
                    progress_callback(percent, f"Processing: {cursor_name}")
                
                output_path, frames, width, height, error = self.stack_frames(cursor_folder, output_dir, target_frames=target_frames, padding_mode=padding_mode, scale=scale)
                
                if error:
                    results.append(f"FAIL {cursor_name}: {error}")
                else:
                    results.append(f"OK {cursor_name}: {output_path.name}")
                    cursor_entries.append({
                        'name': cursor_name,
                        'filename': output_path.name,
                        'frames': frames,
                        'width': width,
                        'height': height,
                        'hotspot': {'x': width//2, 'y': height//2}
                    })
                    # Ask GUI to preview this stacked image (special sentinel via progress callback)
                    if progress_callback:
                        progress_callback(percent, f"__PREVIEW__:{output_path}")
            
            # Create .cape package
            base_name = Path(zip_path).stem
            final_output = Path(output_dir)
            cape_path, cape_error = self.create_cape(final_output, base_name, cursor_entries, fps)
            if cape_error:
                results.append(f"WARN: .cape generation failed: {cape_error}")
            else:
                results.append(f"OK .cape: {cape_path.name}")
            
            # Cleanup temp directory
            shutil.rmtree(temp_dir)
            
            return results, None
            
        except Exception as e:
            return None, f"Error processing ZIP: {str(e)}"


class CursorProcessorGUI:
    """PySimpleGUI interface for Cursor Processor"""
    
    def __init__(self):
        self.processor = CursorProcessor()
        self.window = None
        
    def create_layout(self):
        """Create the GUI layout"""
        layout = [
            [sg.Text('Mousecape Cursor Pack Processor', font=('Arial', 14, 'bold'))],
            [sg.Text('_' * 60)],
            
            # File Selection Section
            [sg.Text('SELECT CURSOR PACK', font=('Arial', 11, 'bold'))],
            [sg.Text('ZIP File:'), 
             sg.Input(key='zip_file', disabled=True, size=(40, 1)), 
             sg.FileBrowse(file_types=(('ZIP Files', '*.zip'),))],
            
            [sg.Text('Output Folder:'), 
             sg.Input(key='output_dir', default_text=str(Path.home() / 'Desktop'), size=(40, 1)),
             sg.FolderBrowse()],
            
            [sg.Text('_' * 60)],
            
            # Settings Section
            [sg.Text('ANIMATION SETTINGS', font=('Arial', 11, 'bold'))],
            [sg.Text('Frames Per Second (FPS):'),
             sg.Spin(values=list(range(1, 61)), initial_value=24, size=(5, 1), key='fps'),
             sg.Text('  Scale:'),
             sg.Combo(['1x (Original)', '2x (2x Size)', '0.5x (Half)'], 
                     default_value='1x (Original)', key='scale', readonly=True)],
            # New stack settings
            [sg.Text('Stack target:'),
             sg.Spin(values=list(range(1, 129)), initial_value=23, size=(5,1), key='target_frames'),
             sg.Text('  Padding:'),
             sg.Combo(['repeat_first', 'repeat_last', 'mirror'], default_value='repeat_first', key='padding_mode', readonly=True)],
            
            [sg.Text('_' * 60)],
            
            # Progress + Preview Section
            [sg.Text('PROGRESS', font=('Arial', 11, 'bold'))],
            [sg.ProgressBar(100, size=(40, 25), key='progress_bar'), sg.Column([[sg.Image(key='preview', size=(200,200))]])],
            [sg.Multiline(size=(62, 10), key='output_text', disabled=True, 
                         background_color='black', text_color='lightgreen')],
            
            [sg.Text('_' * 60)],
            
            # Button Section
            [sg.Button('Process Cursor Pack', size=(20, 1), button_color=('white', 'green')),
             sg.Button('Clear', size=(10, 1)),
             sg.Button('Exit', size=(10, 1))],
            
            [sg.Text('Made with love for macOS cursor designers', 
                    font=('Arial', 8), text_color='gray')]
        ]
        return layout
    
    def run(self):
        """Run the GUI"""
        layout = self.create_layout()
        self.window = sg.Window('Cursor Pack Processor', layout, finalize=True)
        
        # Bind window close
        self.window.bind('<Escape>', 'Exit')
        
        while True:
            event, values = self.window.read(timeout=100)
            
            # Handle progress events posted from worker threads
            if event == '-PROGRESS-':
                pct, msg = values[event]
                # If this is a preview sentinel, post a preview event
                if isinstance(msg, str) and msg.startswith('__PREVIEW__:'):
                    preview_path = msg.split('__PREVIEW__:',1)[1]
                    try:
                        # update preview image (main thread)
                        self.window['preview'].update(filename=str(preview_path))
                    except Exception:
                        pass
                    continue
                # Update GUI elements on main thread only
                try:
                    self.window['progress_bar'].update(pct)
                    current_text = self.window['output_text'].get()
                    self.window['output_text'].update(current_text + f"\n{msg}")
                    self.window.refresh()
                except Exception:
                    pass
                continue
            
            if event == '-PREVIEW-':
                # direct preview event (if used)
                try:
                    self.window['preview'].update(filename=str(values[event]))
                except Exception:
                    pass
                continue
            
            if event == '-ENABLE_BUTTON-':
                try:
                    self.window['Process Cursor Pack'].update(disabled=False)
                except Exception:
                    pass
                continue
            
            if event == sg.WINDOW_CLOSED or event == 'Exit':
                break
            
            if event == 'Clear':
                self.window['zip_file'].update('')
                self.window['output_text'].update('')
                self.window['progress_bar'].update(0)
                try:
                    self.window['preview'].update(data=None)
                except Exception:
                    pass
            
            if event == 'Process Cursor Pack':
                if not values['zip_file']:
                    sg.popup_error('Please select a ZIP file!')
                    continue
                
                # Disable button during processing
                self.window['Process Cursor Pack'].update(disabled=True)
                
                # Read settings
                target_frames = int(values.get('target_frames', 23))
                padding_mode = values.get('padding_mode', 'repeat_first')
                
                # Run processing in thread
                thread = threading.Thread(
                    target=self._process_thread,
                    args=(values['zip_file'], values['output_dir'], values['fps'], values['scale'], target_frames, padding_mode)
                )
                thread.daemon = True
                thread.start()
        
        self.window.close()
    
    def _process_thread(self, zip_file, output_dir, fps, scale, target_frames, padding_mode):
        """Process ZIP in background thread"""
        try:
            # Parse scale value
            scale_map = {'1x (Original)': 1, '2x (2x Size)': 2, '0.5x (Half)': 0.5}
            scale_value = scale_map.get(scale, 1)
            
            # Create output directory
            base_name = Path(zip_file).stem
            final_output = Path(output_dir) / f"{base_name}_processed"
            final_output.mkdir(parents=True, exist_ok=True)
            
            # Define a thread-safe progress poster that writes an event to the GUI
            def progress_update(percent, message):
                # Post preview sentinel messages and normal progress messages
                try:
                    # write_event_value is thread-safe
                    self.window.write_event_value('-PROGRESS-', (percent, message))
                except Exception:
                    # If window is closed or unavailable, ignore
                    pass
            
            results, error = self.processor.process_zip(
                zip_file, 
                final_output, 
                fps, 
                scale_value,
                progress_update,
                target_frames=target_frames,
                padding_mode=padding_mode
            )
            
            # Final update
            if error:
                self.window.write_event_value('-PROGRESS-', (100, f"ERROR: {error}"))
            else:
                # results is a list of status lines
                output_msg = '\n'.join([f"  {r}" for r in results])
                self.window.write_event_value('-PROGRESS-', (100, f"Generated outputs:\n{output_msg}"))
                # Also show the processed folder path
                self.window.write_event_value('-PROGRESS-', (100, f"Output folder: {final_output}"))
                self.window.write_event_value('-FINISHED-', True)
        
        except Exception as e:
            try:
                self.window.write_event_value('-PROGRESS-', (100, f"ERROR: {str(e)}"))
                self.window.write_event_value('-FINISHED-', True)
            except Exception:
                pass
        
        finally:
            # Re-enable the button on the main thread
            try:
                self.window.write_event_value('-ENABLE_BUTTON-', True)
            except Exception:
                pass


def main():
    """Main entry point"""
    gui = CursorProcessorGUI()
    gui.run()


if __name__ == '__main__':
    main()
