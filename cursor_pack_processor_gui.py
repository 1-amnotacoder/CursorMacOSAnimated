#!/usr/bin/env python3
"""
Mousecape Cursor Pack Processor - PySimpleGUI Edition
Processes cursor animation frames and generates Mousecape-compatible packs

This updated version fixes a Tk/Tcl threading issue by ensuring all GUI updates
are performed on the main thread. Worker threads post events to the GUI thread
using `window.write_event_value` which is thread-safe.
"""

import os
import sys
import zipfile
import shutil
import threading
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
        
    def validate_dimensions(self, images):
        """Check all images have same dimensions"""
        if not images:
            return None, None, None
        
        first = Image.open(images[0])
        width, height = first.size
        
        for img_path in images[1:]:
            img = Image.open(img_path)
            if img.size != (width, height):
                return None, None, f"Image size mismatch: {img_path} is {img.size}, expected {(width, height)}"
        
        return width, height, None
    
    def natural_sort_key(self, path):
        """Sort paths with natural number ordering (frame_1, frame_2, ..., frame_10)"""
        import re
        filename = Path(path).stem
        return [int(text) if text.isdigit() else text.lower() 
                for text in re.split(r'(\d+)', filename)]
    
    def stack_frames(self, folder_path, output_dir, fps=24, scale=1):
        """Stack PNG frames vertically"""
        try:
            png_files = sorted(Path(folder_path).glob('*.png'), key=self.natural_sort_key)
            
            if not png_files:
                return None, f"No PNG files found in {folder_path}"
            
            # Validate dimensions
            width, height, error = self.validate_dimensions([str(p) for p in png_files])
            if error:
                return None, error
            
            # Apply scale
            if scale != 1:
                width = int(width * scale)
                height = int(height * scale)
            
            # Create stacked image
            stacked_height = height * len(png_files)
            stacked = Image.new('RGBA', (width, stacked_height), (0, 0, 0, 0))
            
            for i, png_file in enumerate(png_files):
                frame = Image.open(png_file).convert('RGBA')
                
                if scale != 1:
                    frame = frame.resize((width, height), Image.Resampling.LANCZOS)
                
                stacked.paste(frame, (0, i * height), frame)
            
            # Save stacked image
            cursor_name = Path(folder_path).name
            output_path = Path(output_dir) / f"{cursor_name}_stacked.png"
            stacked.save(output_path, 'PNG')
            
            return output_path, None
            
        except Exception as e:
            return None, f"Error processing {folder_path}: {str(e)}"
    
    def process_zip(self, zip_path, output_dir, fps=24, scale=1, progress_callback=None):
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
            
            # Find cursor folders
            cursor_folders = [d for d in temp_dir.iterdir() if d.is_dir()]
            total_cursors = len(cursor_folders)
            
            if not cursor_folders:
                return None, f"No cursor folders found in ZIP"
            
            results = []
            for idx, cursor_folder in enumerate(sorted(cursor_folders)):
                cursor_name = cursor_folder.name
                
                if progress_callback:
                    progress_callback(int((idx / total_cursors) * 100), f"Processing: {cursor_name}")
                
                output_path, error = self.stack_frames(cursor_folder, output_dir, fps, scale)
                
                if error:
                    results.append(f"FAIL {cursor_name}: {error}")
                else:
                    results.append(f"OK {cursor_name}: {output_path.name}")
            
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
             sg.Spin(values=list(range(1, 61)), default_value=24, size=(5, 1), key='fps'),
             sg.Text('  Scale:'),
             sg.Combo(['1x (Original)', '2x (2x Size)', '0.5x (Half)'], 
                     default_value='1x (Original)', key='scale', readonly=True)],
            
            [sg.Text('_' * 60)],
            
            # Progress Section
            [sg.Text('PROGRESS', font=('Arial', 11, 'bold'))],
            [sg.ProgressBar(100, size=(58, 25), key='progress_bar')],
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
                # Update GUI elements on main thread only
                try:
                    self.window['progress_bar'].update(pct)
                    current_text = self.window['output_text'].get()
                    self.window['output_text'].update(current_text + f"\n{msg}")
                    self.window.refresh()
                except Exception:
                    pass
                continue
            
            if event == sg.WINDOW_CLOSED or event == 'Exit':
                break
            
            if event == 'Clear':
                self.window['zip_file'].update('')
                self.window['output_text'].update('')
                self.window['progress_bar'].update(0)
            
            if event == 'Process Cursor Pack':
                if not values['zip_file']:
                    sg.popup_error('Please select a ZIP file!')
                    continue
                
                # Disable button during processing
                self.window['Process Cursor Pack'].update(disabled=True)
                
                # Run processing in thread
                thread = threading.Thread(
                    target=self._process_thread,
                    args=(values['zip_file'], values['output_dir'], values['fps'], values['scale'])
                )
                thread.daemon = True
                thread.start()
        
        self.window.close()
    
    def _process_thread(self, zip_file, output_dir, fps, scale):
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
                # Post a custom event to the main GUI thread; write_event_value is thread-safe
                try:
                    self.window.write_event_value('-PROGRESS-', (percent, message))
                except Exception:
                    # If window is closed or unavailable, ignore
                    pass
            
            results, error = self.processor.process_zip(
                zip_file, 
                final_output, 
                fps, 
                scale_value,
                progress_update
            )
            
            # Final update
            if error:
                self.window.write_event_value('-PROGRESS-', (100, f"ERROR: {error}"))
            else:
                self.window.write_event_value('-PROGRESS-', (100, "Processing Complete!"))
                output_msg = '\n'.join([f"  {r}" for r in results])
                self.window.write_event_value('-PROGRESS-', (100, f"Generated cursors:\n{output_msg}\nOutput: {final_output}"))
                self.window.write_event_value('-PROGRESS-', (100, "__DONE__"))
                self.window.write_event_value('-PROCESSED_PATH-', str(final_output))
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
