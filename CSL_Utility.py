import csv
import os
from tkinter import *
from tkinter import ttk
from tkinter import filedialog, messagebox
from tkinter import TclError
from typing import List, Dict, Optional, Tuple
from dataclasses import dataclass
import xml.etree.ElementTree as ET
from pathlib import Path
import logging
import platform
import datetime
from enum import Enum

# Version information
VERSION = "0.7"

class MergeMode(Enum):
    KEEP_ALL = "Keep all records"
    KEEP_RECENT = "Keep most recent"
    SMART_MERGE = "Smart merge"

# Set up logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
)

@dataclass
class ContestRecord:
    callsign: str
    locator: str = ""
    exchange: str = ""
    comment: str = ""

    def to_list(self) -> List[str]:
        return [self.callsign, self.locator, self.exchange, self.comment]

    @classmethod
    def from_list(cls, data: List[str]) -> 'ContestRecord':
        if not data:
            raise ValueError("Cannot create ContestRecord from empty data")
        return cls(*data) if len(data) >= 4 else cls(data[0], *([""] * (4 - len(data))))

    def has_more_than_callsign(self) -> bool:
        """Return True if record has any data beyond the callsign."""
        return bool(self.locator.strip() or self.exchange.strip() or self.comment.strip())

    def __eq__(self, other) -> bool:
        if not isinstance(other, ContestRecord):
            return False
        return (self.callsign.upper() == other.callsign.upper() and 
                self.locator.strip() == other.locator.strip() and 
                self.exchange.strip() == other.exchange.strip() and 
                self.comment.strip() == other.comment.strip())

class ContestLogManager:
    """Manages contest log records and file operations."""
    
    HEADER = f"# Minos CSL Utility by G4CTP v{VERSION}\n# <Callsign>, <Locator>, <Exchange>, <Comment>"
    SUPPORTED_FORMATS = {'.csl', '.edi', '.adi', '.adif', '.minos'}

    def __init__(self):
        self.records: List[ContestRecord] = []
        self.current_file: Optional[Path] = None  # Changed from Path() to None
        self.merge_mode: MergeMode = MergeMode.KEEP_ALL
        self.remove_callsign_only: bool = False
        self._observers: List[callable] = []
        self.has_unsaved_changes: bool = False

    def load_file(self, filepath: str, progress_callback: Optional[callable] = None) -> None:
        """Generic file loader that determines format from extension."""
        try:
            path = Path(filepath)
            if not path.exists():
                raise FileNotFoundError(f"File not found: {filepath}")

            extension = path.suffix.lower()
            if extension not in self.SUPPORTED_FORMATS:
                raise ValueError(f"Unsupported file format: {extension}")
                
            file_size = os.path.getsize(filepath)
            initial_count = len(self.records)
            logging.info(f"Starting load of {file_size/1024:.1f}KB file. Merge mode is {self.merge_mode.value}")
            
            # Wrap progress callback to ensure it's between 0-100
            def bounded_progress(percentage: float):
                if progress_callback:
                    progress_callback(min(max(percentage, 0), 100))
            
            if extension == '.csl':
                self.load_csl(filepath, bounded_progress)
            elif extension == '.edi':
                self.load_edi(filepath, bounded_progress)
            elif extension in {'.adi', '.adif'}:
                self.load_adif(filepath, bounded_progress)
            elif extension == '.minos':
                self.load_minos(filepath, bounded_progress)

            final_count = len(self.records)
            logging.info(f"Finished loading. Records count: {final_count}")
            
            self.current_file = path
            self.has_unsaved_changes = True
            self.notify_observers()
            
        except Exception as e:
            logging.error(f"Failed to load {Path(filepath).suffix} file: {str(e)}")
            raise

    def load_edi(self, filename: str, progress_callback: Optional[callable] = None) -> None:
        """Load EDI format file with progress tracking."""
        total_size = os.path.getsize(filename)
        bytes_read = 0

        with open(filename, "r", encoding='utf-8') as f:
            lines = f.readlines()
            comments: Dict[str, str] = {}
            in_qso_section = False
            in_remarks = False
            
            # Calculate total lines for progress
            total_lines = len(lines)
            processed_lines = 0

            # First pass: collect comments
            for line in lines:
                line = line.strip()
                processed_lines += 1
                if progress_callback:
                    progress = (processed_lines / (total_lines * 2)) * 100  # First pass = 0-50%
                    progress_callback(progress)

                if line.startswith('[Remarks]'):
                    in_remarks = True
                    continue
                elif line.startswith('[QSORecords;'):
                    in_remarks = False
                    continue

                if in_remarks and line:
                    try:
                        fields = line.split(';')
                        if len(fields) >= 4:
                            comments[fields[2].strip()] = fields[3].strip()
                    except IndexError:
                        continue

            # Reset for second pass
            processed_lines = 0
            in_qso_section = False

            # Second pass: process QSOs
            for line in lines:
                line = line.strip()
                processed_lines += 1
                if progress_callback:
                    progress = 50 + (processed_lines / (total_lines * 2)) * 100  # Second pass = 50-100%
                    progress_callback(min(progress, 100))

                if line.startswith('[QSORecords;'):
                    in_qso_section = True
                    continue
                elif line.startswith('[END;'):
                    break

                if in_qso_section and line:
                    try:
                        fields = line.split(';')
                        if len(fields) >= 10:  # Ensure we have enough fields
                            record = ContestRecord(
                                callsign=fields[2].strip(),
                                locator=fields[9].strip(),
                                exchange=fields[8].strip(),
                                comment=comments.get(fields[2].strip(), "")
                            )
                            self.add_or_merge_record(record)
                    except IndexError:
                        continue

    def load_adif(self, filename: str, progress_callback: Optional[callable] = None) -> None:
        """Load ADIF format file with progress tracking."""
        total_size = os.path.getsize(filename)
        bytes_read = 0

        with open(filename, "r", encoding='utf-8') as f:
            content = f.read()
            
            if '<EOH>' in content:
                _, qsos = content.split('<EOH>', 1)
            else:
                qsos = content

            total_length = len(qsos)
            processed_length = 0

            while qsos.strip():
                eor_index = qsos.find('<EOR>')
                if eor_index == -1:
                    break

                qso = qsos[:eor_index].strip()
                qsos = qsos[eor_index + 5:].strip()
                
                processed_length += eor_index + 5
                if progress_callback:
                    progress = (processed_length / total_length) * 100
                    progress_callback(min(progress, 100))

                record = ContestRecord(
                    callsign=self.extract_adif_field(qso, 'CALL'),
                    locator=self.extract_adif_field(qso, 'GRIDSQUARE'),
                    exchange=self.extract_adif_field(qso, 'QTH'),
                    comment=self.extract_adif_field(qso, 'COMMENT')
                )
                
                if record.callsign:  # Only add records with a callsign
                    self.add_or_merge_record(record)

    def load_minos(self, filename: str, progress_callback: Optional[callable] = None) -> None:
        """Load Minos format file with progress tracking and comprehensive error handling.
        
        Args:
            filename (str): Path to the Minos file to load
            progress_callback (Optional[callable]): Callback function for progress updates
            
        Raises:
            FileNotFoundError: If the file doesn't exist
            IOError: If there are issues reading the file
            ValueError: If the file format is invalid
            ET.ParseError: If the XML is malformed
            
        The method processes Minos XML format files, which contain QSO records with
        detailed contact information. It supports progress tracking and implements
        the specified merge mode for duplicate contacts.
        """
        try:
            total_size = os.path.getsize(filename)
            bytes_read = 0

            with open(filename, 'r', encoding='utf-8') as file:
                # Read and clean XML content
                content = file.read()
                stream_start = content.find('<stream:stream')
                if stream_start == -1:
                    raise ValueError("Invalid Minos file format: No stream element found")
                    
                clean_content = content[stream_start:]
                if '</stream:stream>' not in clean_content:
                    clean_content += '</stream:stream>'

                try:
                    root = ET.fromstring(clean_content)
                except ET.ParseError as e:
                    raise ValueError(f"Invalid XML in Minos file: {str(e)}")

                # Define XML namespaces
                ns = "{minos:iq:rpc}"
                ns_client = "{minos:client}"
                
                # Count total IQ elements for progress tracking
                total_iqs = len(root.findall(f".//{ns_client}iq"))
                if total_iqs == 0:
                    raise ValueError("No QSO records found in file")
                    
                processed_iqs = 0
                qso_count = 0
                
                # Process each IQ element
                for iq in root.findall(f".//{ns_client}iq"):
                    processed_iqs += 1
                    
                    # Update progress
                    if progress_callback:
                        progress = (processed_iqs / total_iqs) * 100
                        progress_callback(min(progress, 100))  # Ensure progress doesn't exceed 100%

                    # Find and process query element
                    query = iq.find(f"{ns}query")
                    if query is None:
                        continue

                    # Find and process method call element
                    method_call = query.find(f"{ns}methodCall")
                    if method_call is None:
                        continue

                    # Check if this is a QSO record
                    method_name = method_call.find(f"{ns}methodName")
                    if method_name is None or method_name.text != "MinosLogQSO":
                        continue

                    # Process QSO parameters
                    params = method_call.find(f"{ns}params/{ns}param/{ns}value/{ns}struct")
                    if params is not None:
                        # Extract QSO data from XML structure
                        qso_data = {}
                        for member in params.findall(f"{ns}member"):
                            name_elem = member.find(f"{ns}name")
                            value_elem = member.find(f"{ns}value")
                            if name_elem is not None and value_elem is not None:
                                for child in value_elem:
                                    qso_data[name_elem.text] = child.text
                                    break

                        # Create contest record if valid callsign exists
                        if 'callRx' in qso_data and qso_data['callRx']:
                            # Combine comments if they exist and are different
                            comments = []
                            if qso_data.get('commentsTx'):
                                comments.append(qso_data['commentsTx'])
                            if qso_data.get('commentsRx') and qso_data['commentsRx'] != qso_data.get('commentsTx'):
                                comments.append(qso_data['commentsRx'])
                                
                            # Create and add the record
                            record = ContestRecord(
                                callsign=qso_data.get('callRx', '').strip(),
                                locator=qso_data.get('locRx', '').strip(),
                                exchange=qso_data.get('exchangeRx', '').strip(),
                                comment=" | ".join(comments)
                            )
                            self.add_or_merge_record(record)
                            qso_count += 1

                # Log completion
                logging.info(f"Finished loading Minos file. Processed {qso_count} QSOs from {processed_iqs} records.")
                if qso_count == 0:
                    logging.warning("No valid QSO records were found in the file.")

        except FileNotFoundError:
            logging.error(f"Minos file not found: {filename}")
            raise
        except ET.ParseError as e:
            logging.error(f"XML parsing error in Minos file: {str(e)}")
            raise ValueError(f"Failed to parse Minos file: {str(e)}")
        except Exception as e:
            logging.error(f"Unexpected error loading Minos file: {str(e)}")
            raise IOError(f"Failed to load Minos file: {str(e)}")

    def load_csl(self, filename: str, progress_callback: Optional[callable] = None) -> None:
        """Load CSL format file with progress tracking."""
        total_size = os.path.getsize(filename)
        
        with open(filename, "r", encoding='utf-8') as f:
            # Read all lines for progress tracking
            lines = f.readlines()
            total_lines = len(lines)
            processed_lines = 0
            
            reader = csv.reader(lines)
            first_line = next(reader, None)
            
            if first_line and not first_line[0].startswith('#'):
                self.add_or_merge_record(ContestRecord.from_list(
                    [field.strip() for field in first_line]))
                processed_lines += 1
                if progress_callback:
                    progress_callback((processed_lines / total_lines) * 100)
            
            for row in reader:
                processed_lines += 1
                if row:  # Skip empty rows
                    self.add_or_merge_record(ContestRecord.from_list(
                        [field.strip() for field in row]))
                
                if progress_callback:
                    progress_callback((processed_lines / total_lines) * 100)

    def save_csl(self, filename: str) -> None:
        """Save to CSL format with error handling."""
        try:
            with open(filename, "w", newline='', encoding='utf-8') as f:
                f.write(self.HEADER + '\n')
                writer = csv.writer(f)
                writer.writerows([r.to_list() for r in self.records])
            self.has_unsaved_changes = False
            self.notify_observers()
        except Exception as e:
            raise IOError(f"Failed to save file: {str(e)}")

    def set_merge_mode(self, mode: MergeMode):
        """Set the merge mode."""
        self.merge_mode = mode
        logging.info(f"Merge mode set to {mode.value}")

    def set_remove_callsign_only(self, value: bool):
        """Set whether to remove callsign-only records."""
        self.remove_callsign_only = value
        logging.info(f"Remove callsign-only records set to {value}")

    def add_or_merge_record(self, new_record: ContestRecord) -> None:
        """Add new record or merge with existing one based on merge mode."""
        if not new_record.callsign:
            return
            
        # Only check for more than callsign if the flag is set
        if self.remove_callsign_only and not new_record.has_more_than_callsign():
            return

        existing_records = [r for r in self.records if r.callsign.upper() == new_record.callsign.upper()]

        if self.merge_mode == MergeMode.KEEP_ALL:
            if new_record not in self.records:
                self.records.append(new_record)
                
        elif self.merge_mode == MergeMode.KEEP_RECENT:
            if existing_records:
                self.records.remove(existing_records[0])
            self.records.append(new_record)
            
        elif self.merge_mode == MergeMode.SMART_MERGE:
            if existing_records:
                existing_record = existing_records[0]
                merged_record = ContestRecord(
                    callsign=existing_record.callsign,
                    locator=new_record.locator if new_record.locator.strip() else existing_record.locator,
                    exchange=new_record.exchange if new_record.exchange.strip() else existing_record.exchange,
                    comment=new_record.comment if new_record.comment.strip() else existing_record.comment
                )
                self.records[self.records.index(existing_record)] = merged_record
            else:
                self.records.append(new_record)
        
        self.has_unsaved_changes = True
        self.notify_observers()

    def add_observer(self, callback: callable):
        """Add observer for record changes."""
        self._observers.append(callback)

    def notify_observers(self):
        """Notify observers of record changes."""
        for callback in self._observers:
            callback()

    def reset(self) -> None:
        """Clear all records."""
        if self.records:  # Only set unsaved changes if there were records
            self.has_unsaved_changes = True
        self.records = []
        self.notify_observers()

    def extract_adif_field(self, qso: str, field: str) -> str:
        """Extract field from ADIF record."""
        field_start = qso.upper().find(f'<{field.upper()}')
        if field_start == -1:
            return ""

        field_end = qso.find('>', field_start)
        if field_end == -1:
            return ""

        try:
            length = int(qso[field_start:field_end].split(':')[1].split(':')[0])
            value_start = field_end + 1
            return qso[value_start:value_start + length].strip()
        except (IndexError, ValueError):
            return ""
        
class ContestLogUI:
    def __init__(self):
        self.window = None
        self.progress_frame = None
        self.progress_label = None
        self.progress_var = None
        self.progress_bar = None
        self.status_text = None
        self.count_bar = None
        self.save_button = None
        self.reset_button = None
        self.merge_mode_var = None
        self.remove_callsign_var = None
        self.loading = False
        self.status_messages = []
        self.manager = manager  # Should be set by the caller
        self.system = platform.system()
        
        # Initialize the UI immediately in __init__
        self.setup_ui()

    def setup_ui(self):
        self.window = Tk()
        self.window.geometry("500x600")
        self.window.title(f"Minos CSL Utility v{VERSION} by G4CTP")
        
        # Configure styles
        style = ttk.Style()
        style.configure('Default.TButton', background='white')
        style.configure('Highlight.TButton', background='yellow')
        
        # Create main frame with padding
        main_frame = ttk.Frame(self.window, padding="10")
        main_frame.grid(row=0, column=0, sticky="nsew")
        
        # Configure grid weights
        self.window.grid_rowconfigure(0, weight=1)
        self.window.grid_columnconfigure(0, weight=1)
        
        # Top section (options and buttons)
        top_section = ttk.Frame(main_frame)
        top_section.grid(row=0, column=0, sticky="ew", pady=(0, 10))

        # Options frame
        options_frame = ttk.LabelFrame(top_section, text="Options", padding="5")
        options_frame.grid(row=0, column=0, sticky="ew", pady=(0, 10))
        
        # Merge options
        self.merge_mode_var = StringVar(value=MergeMode.KEEP_ALL.value)
        
        for idx, mode in enumerate(MergeMode):
            ttk.Radiobutton(
                options_frame,
                text=mode.value,
                variable=self.merge_mode_var,
                value=mode.value,
                command=self.update_merge_mode
            ).grid(row=idx, column=0, sticky="w")

        # Add separator between merge options and checkbox
        ttk.Separator(options_frame, orient='horizontal').grid(
            row=len(MergeMode), column=0, sticky="ew", pady=5)

        # Add checkbox for callsign-only removal
        self.remove_callsign_var = BooleanVar(value=False)
        ttk.Checkbutton(
            options_frame,
            text="Remove callsign-only records",
            variable=self.remove_callsign_var,
            command=self.update_remove_callsign
        ).grid(row=len(MergeMode)+1, column=0, sticky="w")

        # Buttons frame
        button_frame = ttk.LabelFrame(top_section, text="File Operations", padding="5")
        button_frame.grid(row=1, column=0, sticky="ew", pady=(0, 10))

        # Load buttons
        button_configs = [
            ("Load CSL file", lambda: self.load_file([("CSL files", "*.csl"), ("All files", "*.*")])),
            ("Load EDI file", lambda: self.load_file([("EDI files", "*.edi"), ("All files", "*.*")])),
            ("Load ADIF file", lambda: self.load_file([("ADIF files", "*.adi *.adif"), ("All files", "*.*")])),
            ("Load Minos file", lambda: self.load_file([("Minos files", "*.minos"), ("All files", "*.*")]))
        ]

        for idx, (text, command) in enumerate(button_configs):
            ttk.Button(button_frame, text=text, command=command).grid(
                row=idx, column=0, sticky="ew", pady=2)

        # Create frames for save and reset buttons
        bottom_buttons_frame = ttk.Frame(button_frame)
        bottom_buttons_frame.grid(row=len(button_configs), column=0, sticky="ew", pady=2)
        
        # Create the save button based on the operating system
        if self.system == "Darwin":  # macOS
            self.save_button = Button(
                bottom_buttons_frame,
                text="Save CSL file",
                command=self.save_csl,
                highlightbackground='white',
                activebackground='yellow',
                relief=RAISED
            )
        else:  # Windows/Linux
            self.save_button = Button(
                bottom_buttons_frame,
                text="Save CSL file",
                command=self.save_csl,
                bg='white',
                activebackground='yellow',
                relief=RAISED
            )
        self.save_button.grid(row=0, column=0, sticky="ew")

        # Add reset button
        if self.system == "Darwin":  # macOS
            self.reset_button = Button(
                bottom_buttons_frame,
                text="Reset All",
                command=self.confirm_reset,
                highlightbackground='white',
                activebackground='gray90',
                relief=RAISED
            )
        else:  # Windows/Linux
            self.reset_button = Button(
                bottom_buttons_frame,
                text="Reset All",
                command=self.confirm_reset,
                bg='white',
                activebackground='gray90',
                relief=RAISED
            )
        self.reset_button.grid(row=1, column=0, sticky="ew", pady=(2, 0))

        # Configure bottom_buttons_frame grid
        bottom_buttons_frame.grid_columnconfigure(0, weight=1)

        # Create middle section for status and progress
        middle_section = ttk.Frame(main_frame)
        middle_section.grid(row=1, column=0, sticky="nsew", pady=(0, 5))
        main_frame.grid_rowconfigure(1, weight=1)

        # Progress section
        self.progress_frame = ttk.Frame(middle_section)
        self.progress_frame.grid(row=0, column=0, sticky="ew", pady=(0, 5))
        
        # Progress label
        self.progress_label = ttk.Label(self.progress_frame, text="")
        self.progress_label.grid(row=0, column=0, sticky="ew")
        
        # Progress bar
        self.progress_var = DoubleVar()
        self.progress_bar = ttk.Progressbar(
            self.progress_frame,
            variable=self.progress_var,
            maximum=100,
            mode='determinate'
        )
        
        # Progress bar starts hidden
        self.progress_bar.grid(row=1, column=0, sticky="ew")
        self.progress_bar.grid_remove()
        self.progress_label.grid_remove()

        # Status section
        status_frame = ttk.LabelFrame(middle_section, text="Status", padding="5")
        status_frame.grid(row=1, column=0, sticky="nsew")
        middle_section.grid_rowconfigure(1, weight=1)
        
        # Create Text widget with scrollbar
        text_frame = ttk.Frame(status_frame)
        text_frame.grid(row=0, column=0, sticky="nsew")
        status_frame.grid_rowconfigure(0, weight=1)
        status_frame.grid_columnconfigure(0, weight=1)
        
        self.status_text = Text(
            text_frame,
            wrap=WORD,
            height=10,
            font=('TkDefaultFont', 10),
            background='white',
            relief=SUNKEN
        )
        scrollbar = ttk.Scrollbar(text_frame, orient=VERTICAL, command=self.status_text.yview)
        self.status_text.configure(yscrollcommand=scrollbar.set)
        
        scrollbar.grid(row=0, column=1, sticky="ns")
        self.status_text.grid(row=0, column=0, sticky="nsew")
        text_frame.grid_rowconfigure(0, weight=1)
        text_frame.grid_columnconfigure(0, weight=1)
        
        # Make Text widget read-only
        self.status_text.configure(state='disabled')

        # Bottom frame for count bar
        bottom_frame = ttk.Frame(main_frame)
        bottom_frame.grid(row=2, column=0, sticky="ew", pady=(5, 0))
        
        # Separator above count bar
        ttk.Separator(bottom_frame, orient='horizontal').grid(
            row=0, column=0, sticky="ew", pady=5)
        
        # Count display
        self.count_bar = ttk.Label(
            bottom_frame,
            text="Number of rows: 0",
            anchor=W,
            padding=(2, 2, 2, 2)
        )
        self.count_bar.grid(row=1, column=0, sticky="ew")
        bottom_frame.grid_columnconfigure(0, weight=1)

        # Initial button state
        self.update_save_button_state()
        
        # Initial status message
        self.update_status(f"Minos CSL Utility v{VERSION} ready")

    def show_progress(self):
        """Show progress bar and label."""
        self.progress_bar.grid()
        self.progress_label.grid()
        self.loading = True
        self.disable_buttons()
        self.window.update_idletasks()

    def hide_progress(self):
        """Hide progress bar and label."""
        try:
            self.progress_bar.grid_remove()
            self.progress_label.grid_remove()
            self.loading = False
            self.enable_buttons()
            self.window.update_idletasks()
        except TclError as e:
            logging.error(f"Failed to hide progress bar: {str(e)}")

    def update_progress(self, percentage: float, message: str = ""):
        """Update progress bar and message."""
        try:
            self.progress_var.set(percentage)
            if message:
                self.progress_label.configure(text=message)
            self.window.update_idletasks()
        except TclError as e:
            logging.error(f"Failed to update progress: {str(e)}")

    def update_count_bar(self):
        """Update count bar text and ensure visibility."""
        try:
            count = len(self.manager.records)
            self.count_bar.configure(text=f"Number of rows: {count}")
            self.count_bar.grid()  # Ensure visibility
            self.window.update_idletasks()
        except TclError as e:
            logging.error(f"Failed to update count bar: {str(e)}")

    def disable_buttons(self):
        """Disable all buttons during file loading."""
        for widget in self.window.winfo_children():
            if isinstance(widget, (ttk.Button, Button)):
                widget.configure(state='disabled')
        self.window.update_idletasks()

    def enable_buttons(self):
        """Re-enable all buttons after file loading."""
        for widget in self.window.winfo_children():
            if isinstance(widget, (ttk.Button, Button)):
                widget.configure(state='normal')
        self.update_save_button_state()
        self.window.update_idletasks()

    def load_file(self, file_types: List[Tuple[str, str]]):
        """Generic file loading method with progress tracking."""
        filename = filedialog.askopenfilename(filetypes=file_types)
        if filename:
            try:
                self.show_progress()
                file_size = os.path.getsize(filename)
                
                self.update_progress(0, f"Loading {os.path.basename(filename)}...")
                
                if file_size >= 1024 * 1024:
                    self.update_status(f"Loading file ({file_size/1024/1024:.1f} MB)...")
                else:
                    self.update_status(f"Loading file ({file_size/1024:.1f} KB)...")
                
                def progress_callback(percentage):
                    self.update_progress(percentage, f"Loading: {percentage:.1f}%")
                
                self.manager.load_file(filename, progress_callback)
                self.update_status(f"Loaded: {self.truncate_path(filename)}")
                
            except Exception as e:
                messagebox.showerror("Error", str(e))
                self.update_status("Error loading file")
            finally:
                self.hide_progress()
                self.update_count_bar()
                self.window.update_idletasks()

    def save_csl(self):
        """Save to CSL format file with default filename."""
        default_name = f"Minos Archive {datetime.date.today().strftime('%Y-%m-%d')}.csl"
        filename = filedialog.asksaveasfilename(
            defaultextension=".csl",
            initialfile=default_name,
            filetypes=[("CSL files", "*.csl"), ("All files", "*.*")]
        )
        if filename:
            try:
                self.manager.save_csl(filename)
                self.update_status(f"Saved: {self.truncate_path(filename)}")
            except Exception as e:
                messagebox.showerror("Error", str(e))
                self.update_status("Error saving file")

    def update_merge_mode(self):
        """Update merge mode setting in manager."""
        selected_mode = next(mode for mode in MergeMode if mode.value == self.merge_mode_var.get())
        self.manager.set_merge_mode(selected_mode)
        self.update_status(f"Merge mode set to: {selected_mode.value}")

    def update_remove_callsign(self):
        """Update remove callsign-only setting in manager."""
        value = self.remove_callsign_var.get()
        self.manager.set_remove_callsign_only(value)
        self.update_status(f"Remove callsign-only records: {'enabled' if value else 'disabled'}")

    def confirm_reset(self):
        """Show confirmation dialog before resetting."""
        if len(self.manager.records) > 0:
            if messagebox.askyesno("Confirm Reset", 
                                 "Are you sure you want to clear all records? This cannot be undone."):
                self.manager.reset()
                self.update_status("All records cleared")
                self.update_count_bar()
     
    def update_save_button_state(self):
        """Update save button state based on record count and unsaved changes."""
        try:
            has_records = len(self.manager.records) > 0
            self.save_button.config(state='normal' if has_records else 'disabled')
            self.reset_button.config(state='normal' if has_records else 'disabled')
            
            # Update button colors
            button_bg = 'yellow' if (has_records and self.manager.has_unsaved_changes) else 'white'
            if self.system == "Darwin":
                self.save_button.config(highlightbackground=button_bg)
                self.reset_button.config(highlightbackground='white')
            else:
                self.save_button.config(bg=button_bg)
                self.reset_button.config(bg='white')
                
        except TclError as e:
            logging.error(f"Failed to update button state: {str(e)}")

    def update_status(self, message: str):
        """Update status with clean messages and auto-scroll."""
        try:
            status_line = f"{message}\n"
            
            # Store in history
            self.status_messages.append(status_line)
            
            # Update text widget
            self.status_text.configure(state='normal')
            self.status_text.insert(END, status_line)
            self.status_text.see(END)  # Auto-scroll to bottom
            self.status_text.configure(state='disabled')
            
            # Update count bar and ensure visibility
            self.update_count_bar()
            
        except TclError as e:
            logging.error(f"Failed to update status: {str(e)}")

    def update_display(self):
        """Update both status text and save button state."""
        try:
            self.update_save_button_state()
            self.update_count_bar()
            self.window.update_idletasks()
        except TclError as e:
            logging.error(f"Failed to update display: {str(e)}")

    def truncate_path(self, path: str, max_length: int = 100) -> str:
        """Truncate long path names for display."""
        if len(path) <= max_length:
            return path
        return f"...{path[-(max_length-3):]}"

    def run(self):
        """Start the application."""
        self.window.mainloop()


if __name__ == "__main__":
    try:
        # Create and set up the manager
        manager = ContestLogManager()  # Changed from LogManager
        
        # Create the UI
        app = ContestLogUI()
        app.manager = manager
        
        # Add observer for updates
        manager.add_observer(app.update_display)
        
        # Run the application
        app.run()
    except Exception as e:
        logging.critical(f"Application failed to start: {str(e)}")
        raise