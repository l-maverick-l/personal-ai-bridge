from __future__ import annotations

import logging

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (
    QFileDialog,
    QGroupBox,
    QHBoxLayout,
    QLabel,
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
from app.models.settings import AppSettings
from app.ui.setup_wizard import SetupWizard


class MainWindow(QMainWindow):
    def __init__(self, context: AppContext, logger: logging.Logger) -> None:
        super().__init__()
        self._context = context
        self._logger = logger
        self._settings: AppSettings = self._context.settings_store.load()

        self.setWindowTitle("Personal AI Bridge")
        self.resize(1200, 760)

        central_widget = QWidget()
        layout = QVBoxLayout(central_widget)
        splitter = QSplitter(Qt.Horizontal)

        splitter.addWidget(self._build_left_sidebar())
        splitter.addWidget(self._build_center_panel())
        splitter.addWidget(self._build_preview_panel())
        splitter.setStretchFactor(1, 1)

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
        demo_log_button = QPushButton("Write demo log entry")
        demo_log_button.clicked.connect(self.write_demo_log_entry)
        quick_layout.addWidget(setup_button)
        quick_layout.addWidget(demo_log_button)

        recent_group = QGroupBox("Recent commands")
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

        command_group = QGroupBox("Plain-English request")
        command_layout = QVBoxLayout(command_group)
        self.command_input = QPlainTextEdit()
        self.command_input.setPlaceholderText(
            "Examples: Show unread Yahoo emails from today\nSummarize PDFs in my Bills folder"
        )
        command_layout.addWidget(self.command_input)

        results_group = QGroupBox("Results")
        results_layout = QVBoxLayout(results_group)
        self.results_output = QPlainTextEdit()
        self.results_output.setReadOnly(True)
        self.results_output.setPlainText(
            "Phase 1 desktop shell is ready. Mail, file actions, and AI execution will be added in Phase 2."
        )
        results_layout.addWidget(self.results_output)

        actions_group = QGroupBox("Proposed actions and confirmation")
        actions_layout = QVBoxLayout(actions_group)
        self.proposed_actions = QPlainTextEdit()
        self.proposed_actions.setReadOnly(True)
        self.proposed_actions.setPlainText(
            "No live actions yet. Destructive actions and email sending will require explicit confirmation."
        )
        self.confirm_button = QPushButton("Confirm action")
        self.confirm_button.setEnabled(False)
        actions_layout.addWidget(self.proposed_actions)
        actions_layout.addWidget(self.confirm_button)

        layout.addWidget(command_group)
        layout.addWidget(results_group)
        layout.addWidget(actions_group)
        return widget

    def _build_preview_panel(self) -> QWidget:
        tabs = QTabWidget()
        email_preview = QPlainTextEdit()
        email_preview.setReadOnly(True)
        email_preview.setPlainText("Selected email preview will appear here in a later phase.")
        file_preview = QPlainTextEdit()
        file_preview.setReadOnly(True)
        file_preview.setPlainText("Selected file preview will appear here in a later phase.")
        search_context = QPlainTextEdit()
        search_context.setReadOnly(True)
        search_context.setPlainText("Search context and related results will appear here in a later phase.")
        tabs.addTab(email_preview, "Email preview")
        tabs.addTab(file_preview, "File preview")
        tabs.addTab(search_context, "Search context")
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

        self.recent_commands.clear()
        for entry in self._context.action_logger.recent_entries(limit=10):
            line = f"{entry['timestamp']} — {entry['action_type']} — {entry['status']}"
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
        except Exception as exc:  # UI boundary for user-friendly feedback
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

    def write_demo_log_entry(self) -> None:
        self._context.action_logger.record("demo", "main_window", "success")
        self._logger.info("Wrote demo log entry from UI")
        self.results_output.setPlainText(
            "A demo log entry was recorded. Phase 1 logging is working to both SQLite and the log file."
        )
        self.refresh_ui()
