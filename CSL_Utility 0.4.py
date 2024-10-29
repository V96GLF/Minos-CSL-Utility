import csv
import os
from tkinter import *
from tkinter import ttk  # Added for better-looking widgets
from tkinter import filedialog, messagebox
from typing import List, Dict, Optional, Tuple
from dataclasses import dataclass
import xml.etree.ElementTree as ET
from pathlib import Path  # Added for better path handling

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
        # Add validation for empty data
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
    HEADER = "# <Callsign>, <Locator>, <Exchange>, <Comment>"
    SUPPORTED_FORMATS = {'.csl', '.edi', '.adi', '.adif', '.minos'}

    def __init__(self):
        self.records: List[ContestRecord] = []
        self.current_file: Path = Path()
        self.smart_merge: bool = False
        self._observers: List[callable] = []

    def add_observer(self, callback: callable):
        """Add observer for record changes."""
        self._observers.append(callback)

    def notify_observers(self):
        """Notify observers of record changes."""
        for callback in self._observers:
            callback()

    def set_smart_merge(self, value: bool):
        """Set whether to use smart merge or overwrite."""
        self.smart_merge = value

    def load_file(self, filepath: str) -> None:
        """Generic file loader that determines format from extension."""
        path = Path(filepath)
        if not path.exists():
            raise FileNotFoundError(f"File not found: {filepath}")

        extension = path.suffix.lower()
        if extension not in self.SUPPORTED_FORMATS:
            raise ValueError(f"Unsupported file format: {extension}")

        # Clear existing records before loading new file
        self.records.clear()
        
        try:
            # Call appropriate loader based on extension
            if extension == '.csl':
                self.load_csl(filepath)
            elif extension == '.edi':
                self.load_edi(filepath)
            elif extension in {'.adi', '.adif'}:
                self.load_adif(filepath)
            elif extension == '.minos':
                self.load_minos(filepath)

            self.current_file = path
            self.notify_observers()
        except Exception as e:
            raise IOError(f"Failed to load {extension} file: {str(e)}")

    def load_csl(self, filename: str):
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

    def load_edi(self, filename: str):
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
                    exchange=self.extract_adif_field(qso, 'RST_SENT'),
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
        try:
            with open(filename, 'r', encoding='utf-8') as file:
                content = file.read()

            # Find the stream start and clean up content
            stream_start = content.find('<stream:stream')
            if stream_start == -1:
                raise ValueError("No stream element found in file")
                
            clean_content = content[stream_start:]
            if '</stream:stream>' not in clean_content:
                clean_content += '</stream:stream>'

            # Parse XML
            root = ET.fromstring(clean_content)

            # Define namespace prefix for minos:iq:rpc
            ns = "{minos:iq:rpc}"

            # Process each IQ element
            qso_count = 0
            for iq in root.findall(".//{minos:client}iq"):
                # Get the query element
                query = iq.find(f"{ns}query")
                if query is None:
                    continue

                # Get the methodCall element
                method_call = query.find(f"{ns}methodCall")
                if method_call is None:
                    continue

                # Get methodName with namespace
                method_name = method_call.find(f"{ns}methodName")
                if method_name is not None and method_name.text == "MinosLogQSO":
                    # Get params/param/value/struct with proper namespaces
                    params = method_call.find(f"{ns}params/{ns}param/{ns}value/{ns}struct")
                    if params is not None:
                        # Extract QSO data
                        qso_data = {}
                        for member in params.findall(f"{ns}member"):
                            name_elem = member.find(f"{ns}name")
                            value_elem = member.find(f"{ns}value")
                            if name_elem is not None and value_elem is not None:
                                # Get any child element of value (string, i4, etc.)
                                for child in value_elem:
                                    qso_data[name_elem.text] = child.text
                                    break

                        # Create record if we have required fields
                        if 'callRx' in qso_data and qso_data['callRx']:
                            record = ContestRecord(
                                callsign=qso_data.get('callRx', '').strip(),
                                locator=qso_data.get('locRx', '').strip(),
                                exchange=qso_data.get('serialRx', '').strip(),
                                comment=(
                                    f"Time: {qso_data.get('logTime', '')}, "
                                    f"RST: {qso_data.get('rstRx', '')}, "
                                    f"Mode: {qso_data.get('modeTx', '')}, "
                                    f"Serial: {qso_data.get('serialRx', '')}"
                                )
                            )
                            self.add_or_merge_record(record)
                            qso_count += 1

            if not self.records:
                raise ValueError("No QSO records found in the Minos file")

        except Exception as e:
            raise IOError(f"Failed to load Minos file: {str(e)}")
    
    def load_file(self, filepath: str) -> None:
        """Generic file loader that determines format from extension."""
        path = Path(filepath)
        if not path.exists():
            raise FileNotFoundError(f"File not found: {filepath}")

        extension = path.suffix.lower()
        if extension not in self.SUPPORTED_FORMATS:
            raise ValueError(f"Unsupported file format: {extension}")
            
        try:
            # Call appropriate loader based on extension
            if extension == '.csl':
                self.load_csl(filepath)
            elif extension == '.edi':
                self.load_edi(filepath)
            elif extension in {'.adi', '.adif'}:
                self.load_adif(filepath)
            elif extension == '.minos':
                self.load_minos(filepath)

            self.current_file = path
            self.notify_observers()
        except Exception as e:
            raise IOError(f"Failed to load {extension} file: {str(e)}")
        
    def add_or_merge_record(self, new_record: ContestRecord):
        """Add new record or merge with existing one."""
        if not new_record.callsign:  # Skip records without callsign
            return

        if self.smart_merge:
            # Find existing record with same callsign
            existing_records = [r for r in self.records if r.callsign.upper() == new_record.callsign.upper()]
            if existing_records:
                # Merge with first matching record
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
            # Check for identical record
            if new_record not in self.records:
                self.records.append(new_record)

    def save_csl(self, filename: str) -> None:
        """Save to CSL format with error handling."""
        try:
            with open(filename, "w", newline='', encoding='utf-8') as f:
                f.write(self.HEADER + '\n')
                writer = csv.writer(f)
                writer.writerows([r.to_list() for r in self.records])
        except Exception as e:
            raise IOError(f"Failed to save file: {str(e)}")

class ContestLogUI:
    def __init__(self):
        self.manager = ContestLogManager()
        self.setup_ui()
        self.manager.add_observer(self.update_status)

    def setup_ui(self):
        self.window = Tk()
        self.window.geometry("400x500")
        self.window.title("Contest Log Manager")
        
        # Create main frame with padding
        main_frame = ttk.Frame(self.window, padding="10")
        main_frame.pack(fill=BOTH, expand=True)

        # Smart merge frame
        merge_frame = ttk.LabelFrame(main_frame, text="Options", padding="5")
        merge_frame.pack(fill=X, pady=(0, 10))

        self.smart_merge_var = BooleanVar(value=False)
        ttk.Checkbutton(
            merge_frame,
            text="Smart Merge",
            variable=self.smart_merge_var,
            command=self.update_smart_merge
        ).pack(anchor=W)

        # Buttons frame
        button_frame = ttk.LabelFrame(main_frame, text="File Operations", padding="5")
        button_frame.pack(fill=X, pady=(0, 10))

        # Load buttons
        for text, command in [
            ("Load CSL file", self.load_csl),
            ("Load EDI file", self.load_edi),
            ("Load ADIF file", self.load_adif),
            ("Load Minos file", self.load_minos),
            ("Save CSL file", self.save_csl)
        ]:
            ttk.Button(button_frame, text=text, command=command).pack(
                fill=X, pady=2)

        # Status frame
        status_frame = ttk.Frame(main_frame)
        status_frame.pack(fill=X, side=BOTTOM)
        
        self.status_bar = ttk.Label(
            status_frame, 
            text="Ready", 
            relief=SUNKEN, 
            anchor=W)
        self.status_bar.pack(fill=X, pady=2)
        
        self.count_bar = ttk.Label(
            status_frame, 
            text="Number of rows: 0", 
            relief=SUNKEN, 
            anchor=W)
        self.count_bar.pack(fill=X)

    def update_smart_merge(self):
        """Update smart merge setting in manager."""
        self.manager.set_smart_merge(self.smart_merge_var.get())
        self.update_status("Smart merge " + ("enabled" if self.smart_merge_var.get() else "disabled"))

    def update_status(self, message: str = "Ready"):
        """Update status and count bars."""
        self.status_bar.config(text=message)
        self.count_bar.config(text=f"Number of rows: {len(self.manager.records)}")

    def truncate_path(self, path: str, max_length: int = 40) -> str:
        """Truncate long path names for display."""
        if len(path) <= max_length:
            return path
        return f"...{path[-(max_length-3):]}"

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

    def load_csl(self):
        self.load_file([("CSL files", "*.csl"), ("All files", "*.*")])

    def load_edi(self):
        self.load_file([("EDI files", "*.edi"), ("All files", "*.*")])

    def load_adif(self):
        self.load_file([
            ("ADIF files", ".adi .adif"), 
            ("ADI files", "*.adi"),
            ("ADIF files", "*.adif"),
            ("All files", "*.*")
        ])

    def load_minos(self):
        self.load_file([("Minos files", "*.minos"), ("All files", "*.*")])
        
    def save_csl(self):
        filename = filedialog.asksaveasfilename(
            defaultextension=".csl",
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
        self.window.mainloop()

if __name__ == "__main__":
    app = ContestLogUI()
    app.run()
