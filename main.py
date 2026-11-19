"""QuickIngest desktop UI. Requires PySide6: python -m pip install PySide6"""

import sys
from pathlib import Path

from PySide6.QtCore import QObject, QSettings, QThread, QTimer, Signal, Qt
from PySide6.QtWidgets import (
    QApplication, QCheckBox, QComboBox, QFileDialog, QFormLayout, QGroupBox,
    QHBoxLayout, QLabel, QLineEdit, QMainWindow, QMessageBox, QProgressBar,
    QPushButton, QPlainTextEdit, QSpinBox, QVBoxLayout, QWidget,
)

from card_detector import find_camera_cards
from ingest import (
    assign_sequence_names, copy_files, is_destination_inside_card, new_photos,
    next_sequence_number, sanitize_prefix,
)
from lock_detector import scan_images
from unmount import unmount_drive


class ScanWorker(QObject):
    finished = Signal(object)
    failed = Signal(str)

    def __init__(self, dcim_path: Path, extensions: tuple[str, ...], protected_only: bool, destination: Path | None):
        super().__init__()
        self.dcim_path = dcim_path
        self.extensions = extensions
        self.protected_only = protected_only
        self.destination = destination

    def run(self):
        try:
            files = scan_images(self.dcim_path, self.extensions, self.protected_only)
            if self.destination is not None:
                files = new_photos(files, self.destination)
            self.finished.emit(files)
        except Exception as exc:
            self.failed.emit(str(exc))


class IngestWorker(QObject):
    progress = Signal(int, int, str, str)
    finished = Signal(int, int, int)
    failed = Signal(str)

    def __init__(self, files: list[Path], destination: Path, dest_names: list[str] | None = None):
        super().__init__()
        self.files = files
        self.destination = destination
        self.dest_names = dest_names

    def run(self):
        try:
            result = copy_files(self.files, self.destination, self.progress.emit, self.dest_names)
            self.finished.emit(*result)
        except Exception as exc:
            self.failed.emit(str(exc))


class QuickIngestWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("QuickIngest")
        self.setMinimumSize(620, 560)
        self.resize(700, 620)

        self.cards: list[dict] = []
        self.scanned_files: list[Path] = []
        self.scan_thread = None
        self.ingest_thread = None
        self._busy = False
        self._auto_pending = False
        self._known_drives: set[str] = set()
        self._baseline_done = False
        self.settings = QSettings("QuickIngest", "QuickIngest")

        root = QWidget()
        self.setCentralWidget(root)
        layout = QVBoxLayout(root)
        layout.setContentsMargins(18, 18, 18, 18)
        layout.setSpacing(12)

        heading = QLabel("QuickIngest")
        heading.setStyleSheet("font-size: 22px; font-weight: 600;")
        layout.addWidget(heading)
        layout.addWidget(QLabel("Sony camera card photo importer"))

        source_group = QGroupBox("Source")
        source_form = QFormLayout(source_group)
        source_row = QHBoxLayout()
        self.source_combo = QComboBox()
        self.refresh_button = QPushButton("Refresh")
        self.refresh_button.clicked.connect(self.refresh_cards)
        self.unmount_button = QPushButton("Unmount")
        self.unmount_button.clicked.connect(self.unmount_card)
        source_row.addWidget(self.source_combo, 1)
        source_row.addWidget(self.refresh_button)
        source_row.addWidget(self.unmount_button)
        source_form.addRow("Camera card", source_row)
        self.card_status = QLabel("Searching for camera cards...")
        source_form.addRow("Status", self.card_status)
        self.eject_check = QCheckBox("Unmount after ingest")
        source_form.addRow("", self.eject_check)
        layout.addWidget(source_group)

        options_group = QGroupBox("Ingest options")
        options_form = QFormLayout(options_group)
        self.arw_check = QCheckBox("ARW")
        self.jpg_check = QCheckBox("JPG / JPEG")
        self.arw_check.setChecked(True)
        self.jpg_check.setChecked(True)
        types_row = QHBoxLayout()
        types_row.addWidget(self.arw_check)
        types_row.addWidget(self.jpg_check)
        types_row.addStretch(1)
        options_form.addRow("File types", types_row)
        self.protected_check = QCheckBox("Protected files only")
        self.protected_check.setChecked(True)
        options_form.addRow("Selection", self.protected_check)
        self.auto_check = QCheckBox("Auto ingest when a card is inserted")
        options_form.addRow("Auto", self.auto_check)
        rename_row = QHBoxLayout()
        self.rename_check = QCheckBox("Rename to sequence")
        self.prefix_edit = QLineEdit("ABC")
        self.prefix_edit.setFixedWidth(80)
        self.prefix_edit.setPlaceholderText("Prefix")
        rename_row.addWidget(self.rename_check)
        rename_row.addWidget(self.prefix_edit)
        self.digits_spin = QSpinBox()
        self.digits_spin.setRange(1, 9)
        self.digits_spin.setValue(1)
        rename_row.addWidget(self.digits_spin)
        rename_row.addWidget(QLabel("digits"))
        rename_row.addStretch(1)
        self.next_label = QLabel("Next: —")
        rename_row.addWidget(self.next_label)
        options_form.addRow("Naming", rename_row)
        layout.addWidget(options_group)

        destination_group = QGroupBox("Destination")
        destination_row = QHBoxLayout(destination_group)
        self.destination_edit = QLineEdit()
        self.destination_edit.setText(r"C:\Users\User\Pictures")
        self.destination_edit.setPlaceholderText("Choose destination folder")
        self.browse_button = QPushButton("Browse...")
        self.browse_button.clicked.connect(self.choose_destination)
        destination_row.addWidget(self.destination_edit, 1)
        destination_row.addWidget(self.browse_button)
        layout.addWidget(destination_group)

        actions = QHBoxLayout()
        self.scan_button = QPushButton("Scan files")
        self.scan_button.clicked.connect(self.scan_source)
        self.ingest_button = QPushButton("Start ingest")
        self.ingest_button.setEnabled(False)
        self.ingest_button.clicked.connect(lambda: self.start_ingest())
        actions.addWidget(self.scan_button)
        actions.addStretch(1)
        actions.addWidget(self.ingest_button)
        layout.addLayout(actions)

        preview_group = QGroupBox("Preview / Activity")
        preview_layout = QVBoxLayout(preview_group)
        self.summary_label = QLabel("No scan yet.")
        self.summary_label.setWordWrap(True)
        preview_layout.addWidget(self.summary_label)
        self.progress = QProgressBar()
        self.progress.setRange(0, 100)
        self.progress.setValue(0)
        preview_layout.addWidget(self.progress)
        self.log = QPlainTextEdit()
        self.log.setReadOnly(True)
        self.log.setPlaceholderText("Scan results and ingest activity will appear here.")
        preview_layout.addWidget(self.log, 1)
        layout.addWidget(preview_group, 1)

        self.destination_edit.setText(self.settings.value("destination", self.destination_edit.text(), type=str))
        self.prefix_edit.setText(self.settings.value("prefix", self.prefix_edit.text(), type=str))
        self.digits_spin.setValue(self.settings.value("digits", self.digits_spin.value(), type=int))
        self.rename_check.setChecked(self.settings.value("rename", False, type=bool))
        self.auto_check.setChecked(self.settings.value("auto", False, type=bool))
        self.protected_check.setChecked(self.settings.value("protected", True, type=bool))
        self.arw_check.setChecked(self.settings.value("arw", True, type=bool))
        self.jpg_check.setChecked(self.settings.value("jpg", True, type=bool))
        self.eject_check.setChecked(self.settings.value("eject", False, type=bool))

        self.statusBar().showMessage("Ready")

        self.prefix_edit.textChanged.connect(self.update_next_number)
        self.digits_spin.valueChanged.connect(self.update_next_number)
        self.destination_edit.textChanged.connect(self.update_next_number)
        self.update_next_number()

        self.drive_timer = QTimer(self)
        self.drive_timer.timeout.connect(self.refresh_cards)
        self.drive_timer.start(2500)
        self.refresh_cards()

    def closeEvent(self, event):
        self.settings.setValue("destination", self.destination_edit.text())
        self.settings.setValue("prefix", self.prefix_edit.text())
        self.settings.setValue("digits", self.digits_spin.value())
        self.settings.setValue("rename", self.rename_check.isChecked())
        self.settings.setValue("auto", self.auto_check.isChecked())
        self.settings.setValue("protected", self.protected_check.isChecked())
        self.settings.setValue("arw", self.arw_check.isChecked())
        self.settings.setValue("jpg", self.jpg_check.isChecked())
        self.settings.setValue("eject", self.eject_check.isChecked())
        super().closeEvent(event)

    def append_log(self, message: str):
        self.log.appendPlainText(message)

    def refresh_cards(self):
        previous = self.source_combo.currentData()
        try:
            self.cards = find_camera_cards()
        except Exception as exc:
            self.card_status.setText(f"Drive detection error: {exc}")
            return

        self.source_combo.blockSignals(True)
        self.source_combo.clear()
        for card in self.cards:
            drive = str(card["drive"])
            label = card.get("volume_name", "")
            self.source_combo.addItem(f"{drive}  —  {label}", drive)
        if previous:
            index = self.source_combo.findData(previous)
            if index >= 0:
                self.source_combo.setCurrentIndex(index)
        self.source_combo.blockSignals(False)

        if self.cards:
            self.card_status.setText(f"{len(self.cards)} camera card(s) detected")
        else:
            self.card_status.setText("No camera card found. Insert a card containing DCIM.")
        self.scan_button.setEnabled(bool(self.cards) and not self._busy)
        self.unmount_button.setEnabled(bool(self.cards) and not self._busy)

        current = {str(card["drive"]) for card in self.cards}
        new_drives = current - self._known_drives
        # shortcut: insertion noticed on the 2.5s drive poll (max ~2.5s late), upgrade to WM_DEVICECHANGE if that latency matters
        if self._baseline_done and new_drives and self.auto_check.isChecked() and not self._busy:
            self._known_drives = current
            for index, card in enumerate(self.cards):
                if str(card["drive"]) in new_drives:
                    self.source_combo.setCurrentIndex(index)
                    break
            self.append_log(f"Auto ingest: card insertion detected ({', '.join(sorted(new_drives))}).")
            self._auto_pending = True
            self.scan_source()
        elif not self._busy:
            # First pass records the baseline (cards present at launch never auto-trigger); busy holds it so a pending insertion fires later.
            self._known_drives = current
            self._baseline_done = True

    def choose_destination(self):
        folder = QFileDialog.getExistingDirectory(self, "Select destination folder", self.destination_edit.text())
        if folder:
            self.destination_edit.setText(folder)

    def resolve_destination(self) -> Path | None:
        raw = self.destination_edit.text().strip().strip('"')
        if not raw:
            return None
        try:
            return Path(raw).expanduser().resolve()
        except OSError:
            return None

    def update_next_number(self):
        # shortcut: one listdir per keystroke, debounce if a huge/network destination makes typing lag
        prefix = sanitize_prefix(self.prefix_edit.text())
        destination = self.resolve_destination()
        if not prefix or destination is None:
            self.next_label.setText("Next: —")
            return
        number = next_sequence_number(destination, prefix)
        self.next_label.setText(f"Next: {prefix}_{number:0{self.digits_spin.value()}d}")

    def selected_card(self):
        drive = self.source_combo.currentData()
        for card in self.cards:
            if str(card["drive"]) == drive:
                return card
        return None

    def unmount_card(self):
        card = self.selected_card()
        if not card:
            QMessageBox.warning(self, "No source", "Select a camera card first.")
            return
        try:
            unmount_drive(Path(card["drive"]))
        except OSError as exc:
            self.append_log(f"ERROR: unmount failed — {exc}")
            self.statusBar().showMessage("Unmount failed")
            QMessageBox.critical(self, "Unmount failed", f"{exc}\n\nUse the Windows tray 'Safely Remove Hardware' instead.")
            return
        self.append_log(f"Unmounted {card['drive']} — safe to remove the card.")
        self.statusBar().showMessage(f"Unmounted {card['drive']}")

    def scan_source(self):
        card = self.selected_card()
        if not card:
            QMessageBox.warning(self, "No source", "Select a camera card first.")
            return
        extensions = []
        if self.arw_check.isChecked():
            extensions.append(".arw")
        if self.jpg_check.isChecked():
            extensions.extend([".jpg", ".jpeg"])
        if not extensions:
            QMessageBox.warning(self, "No file types", "Select at least one file type.")
            return
        if not Path(card["dcim"]).is_dir():
            QMessageBox.warning(self, "Card unavailable", "The selected card is no longer accessible.")
            self.refresh_cards()
            return

        self.scanned_files = []
        self.ingest_button.setEnabled(False)
        self.scan_button.setEnabled(False)
        self.unmount_button.setEnabled(False)
        self._busy = True
        self.summary_label.setText("Scanning files...")
        self.progress.setRange(0, 0)
        self.statusBar().showMessage("Scanning source card...")

        self.scan_thread = QThread(self)
        self.scan_worker = ScanWorker(
            Path(card["dcim"]), tuple(extensions), self.protected_check.isChecked(), self.resolve_destination()
        )
        self.scan_worker.moveToThread(self.scan_thread)
        self.scan_thread.started.connect(self.scan_worker.run)
        self.scan_worker.finished.connect(self.scan_finished)
        self.scan_worker.failed.connect(self.worker_failed)
        self.scan_worker.finished.connect(self.scan_thread.quit)
        self.scan_worker.failed.connect(self.scan_thread.quit)
        self.scan_thread.finished.connect(self.scan_thread.deleteLater)
        self.scan_thread.start()

    def scan_finished(self, files: list[Path]):
        self.scanned_files = files
        self.progress.setRange(0, 100)
        self.progress.setValue(0)
        arw = sum(p.suffix.lower() == ".arw" for p in files)
        jpg = sum(p.suffix.lower() in {".jpg", ".jpeg"} for p in files)
        self.summary_label.setText(f"Scan complete — {len(files):,} new file(s)\nARW: {arw:,}    JPG: {jpg:,}")
        self.append_log(f"Scan complete: {len(files)} new file(s) to ingest.")
        self.statusBar().showMessage("Scan completed")
        self.scan_button.setEnabled(True)
        self.unmount_button.setEnabled(True)
        self.ingest_button.setEnabled(bool(files))
        self._busy = False
        if self._auto_pending:
            self._auto_pending = False
            if files:
                self.start_ingest(confirm=False)
            else:
                self.append_log("Auto ingest: no matching files found.")

    def worker_failed(self, message: str):
        self.progress.setRange(0, 100)
        self.progress.setValue(0)
        self.summary_label.setText("Operation failed.")
        self.append_log(f"ERROR: {message}")
        self.statusBar().showMessage("Operation failed")
        self.scan_button.setEnabled(True)
        self.unmount_button.setEnabled(True)
        self.ingest_button.setEnabled(False)
        self._busy = False
        self._auto_pending = False
        self.update_next_number()
        QMessageBox.critical(self, "QuickIngest error", message)

    def start_ingest(self, confirm: bool = True):
        card = self.selected_card()
        if not card or not self.scanned_files:
            QMessageBox.warning(self, "Nothing to ingest", "Scan files and select a camera card first.")
            return
        raw_destination = self.destination_edit.text().strip().strip('"')
        if not raw_destination:
            QMessageBox.warning(self, "Destination required", "Choose a destination folder first.")
            return
        destination = Path(raw_destination).expanduser()
        try:
            destination = destination.resolve()
            if is_destination_inside_card(Path(card["drive"]), destination):
                QMessageBox.warning(self, "Invalid destination", "Destination cannot be inside the camera card.")
                return
        except OSError as exc:
            QMessageBox.critical(self, "Invalid destination", str(exc))
            return

        if not Path(card["dcim"]).is_dir():
            QMessageBox.warning(self, "Card unavailable", "The camera card was disconnected.")
            self.refresh_cards()
            return

        files = self.scanned_files.copy()
        dest_names = None
        rename_note = ""
        if self.rename_check.isChecked():
            prefix = sanitize_prefix(self.prefix_edit.text())
            if not prefix:
                QMessageBox.warning(self, "Invalid prefix", "Enter a prefix for the sequence names.")
                return
            dest_names = assign_sequence_names(files, destination, prefix, self.digits_spin.value())
            rename_note = f"\nRenamed to {prefix}_n.*"

        if confirm:
            answer = QMessageBox.question(
                self, "Confirm ingest",
                f"Copy {len(files):,} file(s) to:\n{destination}{rename_note}\n\nExisting files will not be overwritten.",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if answer != QMessageBox.StandardButton.Yes:
                return

        self.set_busy(True)
        self.progress.setRange(0, len(self.scanned_files))
        self.progress.setValue(0)
        self.summary_label.setText(f"Copying {len(self.scanned_files):,} file(s)...")
        self.append_log(f"Starting ingest to {destination}")

        self.ingest_thread = QThread(self)
        self.ingest_worker = IngestWorker(files, destination, dest_names)
        self.ingest_worker.moveToThread(self.ingest_thread)
        self.ingest_thread.started.connect(self.ingest_worker.run)
        self.ingest_worker.progress.connect(self.ingest_progress)
        self.ingest_worker.finished.connect(self.ingest_finished)
        self.ingest_worker.failed.connect(self.worker_failed)
        self.ingest_worker.finished.connect(self.ingest_thread.quit)
        self.ingest_worker.failed.connect(self.ingest_thread.quit)
        self.ingest_thread.finished.connect(self.ingest_thread.deleteLater)
        self.ingest_thread.start()

    def ingest_progress(self, completed: int, total: int, filename: str, status: str):
        self.progress.setRange(0, max(total, 1))
        self.progress.setValue(completed)
        self.append_log(f"[{status}] {filename}")
        self.statusBar().showMessage(f"Copying {completed}/{total}")

    def ingest_finished(self, copied: int, skipped: int, errors: int):
        self.set_busy(False)
        self.summary_label.setText(
            f"Ingest finished\nCopied: {copied:,}    Skipped: {skipped:,}    Errors: {errors:,}"
        )
        self.append_log(f"Finished — copied {copied}, skipped {skipped}, errors {errors}.")
        self.statusBar().showMessage("Ingest completed")
        self.ingest_button.setEnabled(False)
        self.update_next_number()
        if self.eject_check.isChecked():
            if errors == 0:
                self.unmount_card()
            else:
                self.append_log("Auto-unmount skipped: ingest finished with errors.")

    def set_busy(self, busy: bool):
        self._busy = busy
        self.scan_button.setEnabled(not busy)
        self.refresh_button.setEnabled(not busy)
        self.browse_button.setEnabled(not busy)
        self.source_combo.setEnabled(not busy)
        self.arw_check.setEnabled(not busy)
        self.jpg_check.setEnabled(not busy)
        self.protected_check.setEnabled(not busy)
        self.rename_check.setEnabled(not busy)
        self.prefix_edit.setEnabled(not busy)
        self.digits_spin.setEnabled(not busy)
        self.unmount_button.setEnabled(not busy)
        self.ingest_button.setEnabled(not busy and bool(self.scanned_files))


def main():
    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    window = QuickIngestWindow()
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
