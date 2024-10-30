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

    def __eq__(self, other) -> bool:
        if not isinstance(other, ContestRecord):
            return False
        return (self.callsign.upper() == other.callsign.upper() and 
                self.locator.strip() == other.locator.strip() and 
                self.exchange.strip() == other.exchange.strip() and 
                self.comment.strip() == other.comment.strip())

class ContestLogManager:
    """Manages contest log records and file operations."""
    
    HEADER = "# <Callsign>, <Locator>, <Exchange>, <Comment>"
    SUPPORTED_FORMATS = {'.csl', '.edi', '.adi', '.adif', '.minos'}

    def __init__(self):
        self.records: List[ContestRecord] = []
        self.current_file: Path = Path()
        self.smart_merge: bool = False
        self._observers: List[callable] = []
        self.has_unsaved_changes: bool = False

    def load_file(self, filepath: str) -> None:
        """Generic file loader that determines format from extension."""
        path = Path(filepath)
        if not path.exists():
            raise FileNotFoundError(f"File not found: {filepath}")

        extension = path.suffix.lower()
        if extension not in self.SUPPORTED_FORMATS:
            raise ValueError(f"Unsupported file format: {extension}")
            
        try:
            initial_count = len(self.records)
            logging.info(f"Starting load with {initial_count} records. Smart merge is {self.smart_merge}")
            
            if extension == '.csl':
                self.load_csl(filepath)
            elif extension == '.edi':
                self.load_edi(filepath)
            elif extension in {'.adi', '.adif'}:
                self.load_adif(filepath)
            elif extension == '.minos':
                self.load_minos(filepath)

            final_count = len(self.records)
            logging.info(f"Finished loading. Records count: {final_count}")
            
            self.current_file = path
            self.has_unsaved_changes = True
            self.notify_observers()
            
        except Exception as e:
            logging.error(f"Failed to load {extension} file: {str(e)}")
            raise IOError(f"Failed to load {extension} file: {str(e)}")

    def load_csl(self, filename: str) -> None:
        """Load CSL format file."""
        with open(filename, "r", encoding='utf-8') as f:
            reader = csv.reader(f)
            first_line = next(reader, None)
            
            if first_line and not first_line[0].startswith('#'):
                self.add_or_merge_record(ContestRecord.from_list(
                    [field.strip() for field in first_line]))
            
            for row in reader:
                if row:  # Skip empty rows
                    self.add_or_merge_record(ContestRecord.from_list(
                        [field.strip() for field in row]))

    def load_edi(self, filename: str) -> None:
        """Load EDI format file."""
        with open(filename, "r", encoding='utf-8') as f:
            lines = f.readlines()
            comments: Dict[str, str] = {}
            in_qso_section = False
            in_remarks = False

            # First pass: collect comments
            for line in lines:
                line = line.strip()
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

            # Second pass: process QSOs
            for line in lines:
                line = line.strip()
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

    def load_adif(self, filename: str):
        """Load ADIF format file."""
        with open(filename, "r", encoding='utf-8') as f:
            content = f.read()
            
            if '<EOH>' in content:
                _, qsos = content.split('<EOH>', 1)
            else:
                qsos = content

            while qsos.strip():
                eor_index = qsos.find('<EOR>')
                if eor_index == -1:
                    break

                qso = qsos[:eor_index].strip()
                qsos = qsos[eor_index + 5:].strip()

                record = ContestRecord(
                    callsign=self.extract_adif_field(qso, 'CALL'),
                    locator=self.extract_adif_field(qso, 'GRIDSQUARE'),
                    exchange=self.extract_adif_field(qso, 'STX'),
                    comment=self.extract_adif_field(qso, 'COMMENT')
                )
                
                if record.callsign:  # Only add records with a callsign
                    self.add_or_merge_record(record)

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

    def load_minos(self, filename: str) -> None:
        """Load Minos format file."""
        with open(filename, 'r', encoding='utf-8') as file:
            content = file.read()
            stream_start = content.find('<stream:stream')
            if stream_start == -1:
                raise ValueError("No stream element found in file")
                
            clean_content = content[stream_start:]
            if '</stream:stream>' not in clean_content:
                clean_content += '</stream:stream>'

            root = ET.fromstring(clean_content)
            ns = "{minos:iq:rpc}"
            ns_client = "{minos:client}"
            qso_count = 0

            for iq in root.findall(f".//{ns_client}iq"):
                query = iq.find(f"{ns}query")
                if query is None:
                    continue

                method_call = query.find(f"{ns}methodCall")
                if method_call is None:
                    continue

                method_name = method_call.find(f"{ns}methodName")
                if method_name is not None and method_name.text == "MinosLogQSO":
                    params = method_call.find(f"{ns}params/{ns}param/{ns}value/{ns}struct")
                    if params is not None:
                        qso_data = {}
                        for member in params.findall(f"{ns}member"):
                            name_elem = member.find(f"{ns}name")
                            value_elem = member.find(f"{ns}value")
                            if name_elem is not None and value_elem is not None:
                                for child in value_elem:
                                    qso_data[name_elem.text] = child.text
                                    break

                        if 'callRx' in qso_data and qso_data['callRx']:
                            comments = []
                            if qso_data.get('commentsTx'):
                                comments.append(qso_data['commentsTx'])
                            if qso_data.get('commentsRx') and qso_data['commentsRx'] != qso_data.get('commentsTx'):
                                comments.append(qso_data['commentsRx'])
                                
                            record = ContestRecord(
                                callsign=qso_data.get('callRx', '').strip(),
                                locator=qso_data.get('locRx', '').strip(),
                                exchange=qso_data.get('exchangeRx', '').strip(),
                                comment=" | ".join(comments)
                            )
                            self.add_or_merge_record(record)
                            qso_count += 1

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

    def add_or_merge_record(self, new_record: ContestRecord) -> None:
        """Add new record or merge with existing one."""
        if not new_record.callsign:
            return

        if self.smart_merge:
            existing_records = [r for r in self.records if r.callsign.upper() == new_record.callsign.upper()]
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
        else:
            if new_record not in self.records:
                self.records.append(new_record)
        
        self.has_unsaved_changes = True
        self.notify_observers()

    def set_smart_merge(self, value: bool):
        """Set whether to use smart merge or overwrite."""
        self.smart_merge = value
        logging.info(f"Smart merge set to {value}")

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

class ContestLogUI:
    def __init__(self):
        self.system = platform.system()
        self.manager = ContestLogManager()
        self.status_messages = []  # Initialize status messages list
        self.setup_ui()
        self.manager.add_observer(self.update_display)

    def setup_ui(self):
        self.window = Tk()
        self.window.geometry("500x600")
        self.window.title("Minos CSL Utility by G4CTP")
        
        # Configure styles
        style = ttk.Style()
        style.configure('Default.TButton', background='white')
        style.configure('Highlight.TButton', background='yellow')
        
        # Create main frame with padding
        main_frame = ttk.Frame(self.window, padding="10")
        main_frame.pack(fill=BOTH, expand=True)

        # Top section (options and buttons)
        top_section = ttk.Frame(main_frame)
        top_section.pack(fill=X, pady=(0, 10))

        # Smart merge frame
        merge_frame = ttk.LabelFrame(top_section, text="Options", padding="5")
        merge_frame.pack(fill=X, pady=(0, 10))

        self.smart_merge_var = BooleanVar(value=False)
        ttk.Checkbutton(
            merge_frame,
            text="Smart Merge",
            variable=self.smart_merge_var,
            command=self.update_smart_merge
        ).pack(anchor=W)

        # Buttons frame
        button_frame = ttk.LabelFrame(top_section, text="File Operations", padding="5")
        button_frame.pack(fill=X, pady=(0, 10))

        # Load buttons
        for text, command in [
            ("Load CSL file", self.load_csl),
            ("Load EDI file", self.load_edi),
            ("Load ADIF file", self.load_adif),
            ("Load Minos file", self.load_minos)
        ]:
            ttk.Button(button_frame, text=text, command=command).pack(
                fill=X, pady=2)

        # Create frames for save and reset buttons
        bottom_buttons_frame = ttk.Frame(button_frame)
        bottom_buttons_frame.pack(fill=X, pady=2)
        
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
        self.save_button.pack(fill=X)

        # Add reset button with normal styling
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
        self.reset_button.pack(fill=X, pady=(2, 0))

        # Status section with Text widget in a LabelFrame
        status_frame = ttk.LabelFrame(main_frame, text="Status", padding="5")
        status_frame.pack(fill=BOTH, expand=True)
        
        # Create Text widget with scrollbar
        text_frame = ttk.Frame(status_frame)
        text_frame.pack(fill=BOTH, expand=True)
        
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
        
        scrollbar.pack(side=RIGHT, fill=Y)
        self.status_text.pack(side=LEFT, fill=BOTH, expand=True)
        
        # Make Text widget read-only
        self.status_text.configure(state='disabled')

        # Count display at bottom
        self.count_bar = ttk.Label(
            main_frame, 
            text="Number of rows: 0",
            anchor=W)
        self.count_bar.pack(fill=X, pady=(5, 0))

        # Initial button state
        self.update_save_button_state()
        
        # Add initial status message
        self.update_status("Ready")

    def update_status(self, message: str):
        """Update status with clean messages and auto-scroll."""
        status_line = f"{message}\n"
        
        # Store in history
        self.status_messages.append(status_line)
        
        # Update text widget
        self.status_text.configure(state='normal')
        self.status_text.insert(END, status_line)
        self.status_text.see(END)  # Auto-scroll to bottom
        self.status_text.configure(state='disabled')
        
        # Update count bar separately
        self.count_bar.config(text=f"Number of rows: {len(self.manager.records)}")

    def update_display(self):
        """Update both status text and save button state."""
        self.update_save_button_state()
        self.count_bar.config(text=f"Number of rows: {len(self.manager.records)}")

    def update_smart_merge(self):
        """Update smart merge setting in manager."""
        self.manager.set_smart_merge(self.smart_merge_var.get())
        self.update_status("Smart merge " + ("enabled" if self.smart_merge_var.get() else "disabled"))

    def update_save_button_state(self):
        """Update save button state based on record count and unsaved changes."""
        try:
            if len(self.manager.records) == 0:
                self.save_button.config(state='disabled')
                self.reset_button.config(state='disabled')
                if self.system == "Darwin":
                    self.save_button.config(highlightbackground='white')
                    self.reset_button.config(highlightbackground='white')
                else:
                    self.save_button.config(bg='white')
                    self.reset_button.config(bg='white')
            else:
                self.save_button.config(state='normal')
                self.reset_button.config(state='normal')
                if self.manager.has_unsaved_changes:
                    if self.system == "Darwin":
                        self.save_button.config(highlightbackground='yellow')
                    else:
                        self.save_button.config(bg='yellow')
                else:
                    if self.system == "Darwin":
                        self.save_button.config(highlightbackground='white')
                    else:
                        self.save_button.config(bg='white')
        except TclError as e:
            logging.error(f"Failed to update button state: {str(e)}")

    def confirm_reset(self):
        """Show confirmation dialog before resetting."""
        if len(self.manager.records) > 0:
            if messagebox.askyesno("Confirm Reset", 
                                 "Are you sure you want to clear all records? This cannot be undone."):
                self.manager.reset()
                self.update_status("All records cleared")

    def load_file(self, file_types: List[Tuple[str, str]]):
        """Generic file loading method with error handling."""
        filename = filedialog.askopenfilename(filetypes=file_types)
        if filename:
            try:
                self.manager.load_file(filename)
                self.update_status(f"Loaded: {self.truncate_path(filename)}")
            except Exception as e:
                messagebox.showerror("Error", str(e))
                self.update_status("Error loading file")

    def truncate_path(self, path: str, max_length: int = 100) -> str:
        """Truncate long path names for display."""
        if len(path) <= max_length:
            return path
        return f"...{path[-(max_length-3):]}"

    def load_csl(self):
        """Load CSL format file."""
        self.load_file([("CSL files", "*.csl"), ("All files", "*.*")])

    def load_edi(self):
        """Load EDI format file."""
        self.load_file([("EDI files", "*.edi"), ("All files", "*.*")])

    def load_adif(self):
        """Load ADIF format file."""
        self.load_file([
            ("ADIF files", "*.adi *.adif"), 
            ("ADI files", "*.adi"),
            ("ADIF files", "*.adif"),
            ("All files", "*.*")
        ])

    def load_minos(self):
        """Load Minos format file."""
        self.load_file([("Minos files", "*.minos"), ("All files", "*.*")])
        
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

    def run(self):
        """Start the application."""
        self.window.mainloop()

if __name__ == "__main__":
    app = ContestLogUI()
    app.run()