from __future__ import annotations

import logging
from collections.abc import Callable
from pathlib import Path

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (
    QFileDialog,
    QComboBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QPlainTextEdit,
    QSplitter,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from app.core.app_context import AppContext
from app.files.service import DirectoryListing, FileOperationError, FileReadResult
from app.models.settings import AppSettings
from app.ui.setup_wizard import SetupWizard


class MainWindow(QMainWindow):
    def __init__(self, context: AppContext, logger: logging.Logger) -> None:
        super().__init__()
        self._context = context
        self._logger = logger
        self._settings: AppSettings = self._context.settings_store.load()
        self._pending_action: Callable[[], None] | None = None

        self.setWindowTitle("Personal AI Bridge")
        self.resize(1400, 860)

        central_widget = QWidget()
        layout = QVBoxLayout(central_widget)
        splitter = QSplitter(Qt.Horizontal)

        splitter.addWidget(self._build_left_sidebar())
        splitter.addWidget(self._build_center_panel())
        splitter.addWidget(self._build_preview_panel())
        splitter.setStretchFactor(1, 1)
        splitter.setStretchFactor(2, 1)

        layout.addWidget(splitter)
        self.setCentralWidget(central_widget)

        self.refresh_ui()
        if not self._settings.setup_complete:
            QTimer.singleShot(0, self.run_setup_wizard)

    def _build_left_sidebar(self) -> QWidget:
        widget = QWidget()
        layout = QVBoxLayout(widget)

        status_group = QGroupBox("Connection status")
        status_layout = QVBoxLayout(status_group)
        self.yahoo_status_label = QLabel()
        self.folders_status_label = QLabel()
        self.ai_status_label = QLabel()
        self.provider_status_label = QLabel()
        for label in [
            self.yahoo_status_label,
            self.folders_status_label,
            self.ai_status_label,
            self.provider_status_label,
        ]:
            label.setWordWrap(True)
            status_layout.addWidget(label)

        folders_group = QGroupBox("Allowed folders")
        folders_layout = QVBoxLayout(folders_group)
        self.allowed_folders_list = QListWidget()
        add_folder_button = QPushButton("Add folder")
        remove_folder_button = QPushButton("Remove selected")
        add_folder_button.clicked.connect(self.add_allowed_folder)
        remove_folder_button.clicked.connect(self.remove_selected_folder)
        folders_layout.addWidget(self.allowed_folders_list)
        folders_layout.addWidget(add_folder_button)
        folders_layout.addWidget(remove_folder_button)

        quick_actions = QGroupBox("Quick actions")
        quick_layout = QVBoxLayout(quick_actions)
        setup_button = QPushButton("Run setup wizard")
        setup_button.clicked.connect(self.run_setup_wizard)
        refresh_button = QPushButton("Refresh file view")
        refresh_button.clicked.connect(self.list_selected_folder)
        quick_layout.addWidget(setup_button)
        quick_layout.addWidget(refresh_button)

        recent_group = QGroupBox("Recent actions")
        recent_layout = QVBoxLayout(recent_group)
        self.recent_commands = QListWidget()
        recent_layout.addWidget(self.recent_commands)

        layout.addWidget(status_group)
        layout.addWidget(folders_group)
        layout.addWidget(quick_actions)
        layout.addWidget(recent_group)
        return widget

    def _build_center_panel(self) -> QWidget:
        widget = QWidget()
        layout = QVBoxLayout(widget)

        browser_group = QGroupBox("File browsing and reading")
        browser_layout = QVBoxLayout(browser_group)
        browser_form = QFormLayout()
        self.root_selector = QComboBox()
        self.root_selector.currentIndexChanged.connect(self._root_changed)
        self.path_input = QLineEdit()
        self.path_input.setPlaceholderText("Folder or file path inside the selected approved root")
        self.search_input = QLineEdit()
        self.search_input.setPlaceholderText("Enter part of a file or folder name")
        browser_form.addRow("Approved root", self.root_selector)
        browser_form.addRow("Relative path", self.path_input)
        browser_form.addRow("Search name", self.search_input)
        browser_layout.addLayout(browser_form)

        browser_buttons = QHBoxLayout()
        list_button = QPushButton("List folder")
        list_button.clicked.connect(self.list_selected_folder)
        search_button = QPushButton("Search names")
        search_button.clicked.connect(self.search_files)
        read_button = QPushButton("Read file")
        read_button.clicked.connect(self.read_selected_file)
        summarize_button = QPushButton("Summarize file")
        summarize_button.clicked.connect(self.summarize_selected_file)
        browser_buttons.addWidget(list_button)
        browser_buttons.addWidget(search_button)
        browser_buttons.addWidget(read_button)
        browser_buttons.addWidget(summarize_button)
        browser_layout.addLayout(browser_buttons)

        actions_group = QGroupBox("File create, rename, copy, move, and delete")
        actions_layout = QVBoxLayout(actions_group)
        actions_form = QFormLayout()
        self.create_path_input = QLineEdit()
        self.create_path_input.setPlaceholderText("example/notes.txt")
        self.rename_name_input = QLineEdit()
        self.rename_name_input.setPlaceholderText("new-name.txt")
        self.destination_root_selector = QComboBox()
        self.destination_path_input = QLineEdit()
        self.destination_path_input.setPlaceholderText("destination/notes.txt")
        actions_form.addRow("Create file path", self.create_path_input)
        actions_form.addRow("Rename to", self.rename_name_input)
        actions_form.addRow("Destination root", self.destination_root_selector)
        actions_form.addRow("Destination path", self.destination_path_input)
        actions_layout.addLayout(actions_form)

        self.create_content_input = QPlainTextEdit()
        self.create_content_input.setPlaceholderText(
            "Optional text content for a new .txt, .md, .csv, or .json file"
        )
        self.create_content_input.setFixedHeight(140)
        actions_layout.addWidget(self.create_content_input)

        action_buttons = QHBoxLayout()
        create_button = QPushButton("Create file")
        create_button.clicked.connect(self.create_file)
        rename_button = QPushButton("Rename file")
        rename_button.clicked.connect(self.rename_file)
        copy_button = QPushButton("Copy file")
        copy_button.clicked.connect(self.copy_file)
        move_button = QPushButton("Move file")
        move_button.clicked.connect(self.move_file)
        delete_button = QPushButton("Delete file")
        delete_button.clicked.connect(self.request_delete_file)
        action_buttons.addWidget(create_button)
        action_buttons.addWidget(rename_button)
        action_buttons.addWidget(copy_button)
        action_buttons.addWidget(move_button)
        action_buttons.addWidget(delete_button)
        actions_layout.addLayout(action_buttons)

        results_group = QGroupBox("Results")
        results_layout = QVBoxLayout(results_group)
        self.results_output = QPlainTextEdit()
        self.results_output.setReadOnly(True)
        self.results_output.setPlainText(
            "Phase 2 file tools are ready. Choose an approved root folder to browse, read, and manage supported files."
        )
        results_layout.addWidget(self.results_output)

        actions_confirm_group = QGroupBox("Pending confirmation")
        actions_confirm_layout = QVBoxLayout(actions_confirm_group)
        self.proposed_actions = QPlainTextEdit()
        self.proposed_actions.setReadOnly(True)
        self.proposed_actions.setPlainText(
            "Delete requests, move requests, and overwrite requests will appear here before they run."
        )
        self.confirm_button = QPushButton("Confirm action")
        self.confirm_button.setEnabled(False)
        self.confirm_button.clicked.connect(self.confirm_pending_action)
        actions_confirm_layout.addWidget(self.proposed_actions)
        actions_confirm_layout.addWidget(self.confirm_button)

        layout.addWidget(browser_group)
        layout.addWidget(actions_group)
        layout.addWidget(results_group)
        layout.addWidget(actions_confirm_group)
        return widget

    def _build_preview_panel(self) -> QWidget:
        tabs = QTabWidget()
        self.email_preview = QPlainTextEdit()
        self.email_preview.setReadOnly(True)
        self.email_preview.setPlainText("Yahoo Mail work is intentionally deferred until Phase 3.")
        self.file_preview = QPlainTextEdit()
        self.file_preview.setReadOnly(True)
        self.file_preview.setPlainText("Selected file contents will appear here.")
        self.search_context = QPlainTextEdit()
        self.search_context.setReadOnly(True)
        self.search_context.setPlainText("Folder listings and file search results will appear here.")
        tabs.addTab(self.email_preview, "Email preview")
        tabs.addTab(self.file_preview, "File preview")
        tabs.addTab(self.search_context, "Folder/search view")
        return tabs

    def refresh_ui(self) -> None:
        self._settings = self._context.settings_store.load()
        folders = self._context.folder_registry.list_folders()

        self.yahoo_status_label.setText(
            f"Yahoo Mail: {'Configured' if self._settings.yahoo_email else 'Not configured'}"
        )
        self.folders_status_label.setText(
            f"Folder access: {'Configured' if folders else 'Not configured'}"
        )
        self.ai_status_label.setText(
            f"AI: {'Configured' if self._settings.ai_mode != 'skip' and self._settings.provider.provider_type != 'none' else 'Not configured'}"
        )
        provider_label = self._settings.provider.label or "Not configured"
        model_name = self._settings.provider.model_name or "No model selected"
        self.provider_status_label.setText(f"Provider/model: {provider_label} / {model_name}")

        self.allowed_folders_list.clear()
        self.allowed_folders_list.addItems(folders)

        current_root = self.root_selector.currentText()
        destination_root = self.destination_root_selector.currentText()
        self.root_selector.blockSignals(True)
        self.destination_root_selector.blockSignals(True)
        self.root_selector.clear()
        self.destination_root_selector.clear()
        self.root_selector.addItems(folders)
        self.destination_root_selector.addItems(folders)
        self._restore_combo_value(self.root_selector, current_root)
        self._restore_combo_value(self.destination_root_selector, destination_root or current_root)
        self.root_selector.blockSignals(False)
        self.destination_root_selector.blockSignals(False)

        self.recent_commands.clear()
        for entry in self._context.action_logger.recent_entries(limit=10):
            line = f"{entry['timestamp']} — {entry['action_type']} — {entry['status']} — {entry['target']}"
            self.recent_commands.addItem(line)

    def run_setup_wizard(self) -> None:
        wizard = SetupWizard(self._settings, self._context.folder_registry.list_folders(), self)
        if wizard.exec():
            new_settings = wizard.build_settings()
            self._context.settings_store.save(new_settings)
            existing = set(self._context.folder_registry.list_folders())
            selected = set(wizard.selected_folders())
            for folder in existing - selected:
                self._context.folder_registry.remove_folder(folder)
            for folder in selected - existing:
                self._context.folder_registry.add_folder(folder)
            self._context.action_logger.record("setup", "wizard", "success")
            self._logger.info("Setup wizard saved settings")
            wizard.show_saved_message()
            self.refresh_ui()

    def add_allowed_folder(self) -> None:
        folder = QFileDialog.getExistingDirectory(self, "Choose approved folder")
        if not folder:
            return
        try:
            normalized = self._context.folder_registry.add_folder(folder)
            self._context.action_logger.record("folder_add", normalized, "success")
            self._logger.info("Added allowed folder: %s", normalized)
            self.refresh_ui()
        except Exception as exc:
            self._context.action_logger.record("folder_add", folder, "error", str(exc))
            QMessageBox.warning(self, "Could not add folder", str(exc))

    def remove_selected_folder(self) -> None:
        item = self.allowed_folders_list.currentItem()
        if not item:
            return
        folder = item.text()
        answer = QMessageBox.question(
            self,
            "Remove allowed folder",
            f"Remove this approved folder?\n\n{folder}",
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        self._context.folder_registry.remove_folder(folder)
        self._context.action_logger.record("folder_remove", folder, "success")
        self._logger.info("Removed allowed folder: %s", folder)
        self.refresh_ui()

    def list_selected_folder(self) -> None:
        root = self._selected_root()
        if not root:
            return
        try:
            listing = self._context.file_service.list_directory(root, self.path_input.text())
            self._show_directory_listing(listing)
            self._show_result(
                f"Listed folder: {listing.relative_path} inside {listing.root}\n\nItems shown in the Folder/search view tab."
            )
        except FileOperationError as exc:
            self._show_error("Could not list folder", exc)

    def search_files(self) -> None:
        root = self._selected_root()
        if not root:
            return
        try:
            matches = self._context.file_service.search_files(root, self.search_input.text())
            if not matches:
                self.search_context.setPlainText("No matching files or folders were found.")
            else:
                lines = [f"Search results in {root}:"]
                for entry in matches:
                    entry_type = "DIR " if entry.is_dir else "FILE"
                    lines.append(f"[{entry_type}] {entry.relative_path}")
                self.search_context.setPlainText("\n".join(lines))
            self._show_result(f"Found {len(matches)} matching item(s).")
        except FileOperationError as exc:
            self._show_error("Could not search files", exc)

    def read_selected_file(self) -> None:
        root = self._selected_root()
        if not root:
            return
        try:
            read_result = self._context.file_service.read_file(root, self.path_input.text())
            self._show_file(read_result)
            self._show_result(f"Read file: {read_result.relative_path}")
        except FileOperationError as exc:
            self._show_error("Could not read file", exc)

    def summarize_selected_file(self) -> None:
        root = self._selected_root()
        if not root:
            return
        try:
            summary = self._context.file_service.summarize_file(root, self.path_input.text())
            self.results_output.setPlainText(summary)
        except FileOperationError as exc:
            self._show_error("Could not summarize file", exc)

    def create_file(self) -> None:
        root = self._selected_root()
        if not root:
            return
        try:
            created_path = self._context.file_service.create_file(
                root,
                self.create_path_input.text(),
                self.create_content_input.toPlainText(),
            )
            relative_path = self.create_path_input.text().strip()
            self._show_result(f"Created file: {created_path}")
            self.path_input.setText(relative_path)
            self._show_parent_directory_for(relative_path)
        except FileOperationError as exc:
            self._show_error("Could not create file", exc)

    def rename_file(self) -> None:
        root = self._selected_root()
        if not root:
            return
        try:
            renamed_path = self._context.file_service.rename_file(
                root,
                self.path_input.text(),
                self.rename_name_input.text(),
            )
            relative_path = self._relative_to_root(root, renamed_path)
            self._show_result(f"Renamed file to: {renamed_path}")
            self.path_input.setText(relative_path)
            self._show_parent_directory_for(relative_path)
        except FileOperationError as exc:
            self._show_error("Could not rename file", exc)

    def copy_file(self) -> None:
        source_root = self._selected_root()
        destination_root = self.destination_root_selector.currentText().strip()
        if not source_root or not destination_root:
            self._show_result("Choose both a source approved root and a destination approved root.")
            return
        destination_path = self.destination_path_input.text().strip()
        if not destination_path:
            self._show_result("Enter a destination file path for the copy action.")
            return
        try:
            destination_exists = self._context.file_service.destination_exists(
                destination_root,
                destination_path,
            )
        except FileOperationError as exc:
            self._show_error("Could not validate copy destination", exc)
            return
        if destination_exists:
            self._set_pending_action(
                message=(
                    "Copy will overwrite an existing supported file.\n\n"
                    f"Source: {source_root}:{self.path_input.text().strip()}\n"
                    f"Destination: {destination_root}:{destination_path}"
                ),
                action=lambda: self._execute_copy_or_move("copy", overwrite=True),
            )
            return
        self._execute_copy_or_move("copy", overwrite=False)

    def move_file(self) -> None:
        source_root = self._selected_root()
        destination_root = self.destination_root_selector.currentText().strip()
        if not source_root or not destination_root:
            self._show_result("Choose both a source approved root and a destination approved root.")
            return
        destination_path = self.destination_path_input.text().strip()
        if not destination_path:
            self._show_result("Enter a destination file path for the move action.")
            return
        try:
            destination_exists = self._context.file_service.destination_exists(
                destination_root,
                destination_path,
            )
        except FileOperationError as exc:
            self._show_error("Could not validate move destination", exc)
            return
        overwrite_note = " This will also overwrite the existing destination file." if destination_exists else ""
        self._set_pending_action(
            message=(
                "Move requested. Confirm before the file is moved."
                f"{overwrite_note}\n\n"
                f"Source: {source_root}:{self.path_input.text().strip()}\n"
                f"Destination: {destination_root}:{destination_path}"
            ),
            action=lambda: self._execute_copy_or_move("move", overwrite=destination_exists),
        )

    def request_delete_file(self) -> None:
        root = self._selected_root()
        if not root:
            return
        relative_path = self.path_input.text().strip()
        if not relative_path:
            self._show_result("Enter the relative path of the file you want to delete.")
            return
        self._set_pending_action(
            message=(
                "Delete requested. Confirm to move this file into the app's safe trash folder.\n\n"
                f"File: {root}:{relative_path}"
            ),
            action=lambda: self._perform_delete(root, relative_path),
        )

    def confirm_pending_action(self) -> None:
        if not self._pending_action:
            return
        action = self._pending_action
        self._clear_pending_action()
        action()

    def _execute_copy_or_move(self, action_name: str, overwrite: bool) -> None:
        source_root = self._selected_root()
        destination_root = self.destination_root_selector.currentText().strip()
        if not source_root or not destination_root:
            return
        try:
            if action_name == "copy":
                destination = self._context.file_service.copy_file(
                    source_root,
                    self.path_input.text(),
                    destination_root,
                    self.destination_path_input.text(),
                    overwrite=overwrite,
                )
                self._show_result(f"Copied file to: {destination}")
            else:
                destination = self._context.file_service.move_file(
                    source_root,
                    self.path_input.text(),
                    destination_root,
                    self.destination_path_input.text(),
                    overwrite=overwrite,
                )
                moved_relative_path = self.destination_path_input.text().strip()
                self._show_result(f"Moved file to: {destination}")
                self.path_input.setText(moved_relative_path)
                self.root_selector.setCurrentText(destination_root)
                self._show_parent_directory_for(moved_relative_path)
                return
            self._show_parent_directory_for(self.destination_path_input.text().strip())
        except FileOperationError as exc:
            title = "Could not copy file" if action_name == "copy" else "Could not move file"
            self._show_error(title, exc)

    def _perform_delete(self, root: str, relative_path: str) -> None:
        try:
            safe_trash_path = self._context.file_service.delete_file(root, relative_path)
            self._show_result(
                "Deleted file by moving it into safe trash:\n"
                f"{safe_trash_path}"
            )
            self.file_preview.setPlainText("Selected file contents will appear here.")
            self._show_parent_directory_for(relative_path)
        except FileOperationError as exc:
            self._show_error("Could not delete file", exc)

    def _selected_root(self) -> str:
        root = self.root_selector.currentText().strip()
        if not root:
            self._show_result("Add at least one approved folder before using file tools.")
            return ""
        return root

    def _restore_combo_value(self, combo: QComboBox, value: str) -> None:
        if not value:
            return
        index = combo.findText(value)
        if index >= 0:
            combo.setCurrentIndex(index)

    def _relative_to_root(self, root: str, absolute_path: str) -> str:
        return Path(absolute_path).resolve().relative_to(Path(root).resolve()).as_posix()

    def _show_parent_directory_for(self, relative_path: str) -> None:
        parent = Path(relative_path).parent.as_posix()
        if parent == ".":
            parent = ""
        self.path_input.setText(parent)
        self.list_selected_folder()

    def _show_directory_listing(self, listing: DirectoryListing) -> None:
        lines = [f"Folder listing for {listing.root}:{listing.relative_path}"]
        if not listing.entries:
            lines.append("(empty folder)")
        for entry in listing.entries:
            entry_type = "DIR " if entry.is_dir else "FILE"
            size = "" if entry.is_dir else f" ({entry.size} bytes)"
            lines.append(f"[{entry_type}] {entry.relative_path}{size}")
        self.search_context.setPlainText("\n".join(lines))

    def _show_file(self, read_result: FileReadResult) -> None:
        self.file_preview.setPlainText(read_result.content)

    def _show_result(self, message: str) -> None:
        self.results_output.setPlainText(message)
        self.refresh_ui()

    def _show_error(self, title: str, exc: Exception) -> None:
        self.results_output.setPlainText(str(exc))
        QMessageBox.warning(self, title, str(exc))
        self.refresh_ui()

    def _set_pending_action(self, message: str, action: Callable[[], None]) -> None:
        self._pending_action = action
        self.proposed_actions.setPlainText(message)
        self.confirm_button.setEnabled(True)

    def _clear_pending_action(self) -> None:
        self._pending_action = None
        self.proposed_actions.setPlainText(
            "Delete requests, move requests, and overwrite requests will appear here before they run."
        )
        self.confirm_button.setEnabled(False)

    def _root_changed(self) -> None:
        if not self.destination_root_selector.currentText().strip() and self.root_selector.count() > 0:
            self.destination_root_selector.setCurrentIndex(self.root_selector.currentIndex())
