from __future__ import annotations

import logging
import os
import subprocess
import sys
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from PySide6.QtCore import QDate, QPoint, Qt, QTimer
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QTreeWidget,
    QTreeWidgetItem,
    QDateEdit,
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QPlainTextEdit,
    QMenu,
    QScrollArea,
    QSplitter,
    QTabWidget,
    QTextBrowser,
    QVBoxLayout,
    QWidget,
)

from app.core.app_context import AppContext
from app.email.yahoo_service import MailMessageView, MailSummary, OutgoingDraft, YahooMailError
from app.files.service import DirectoryListing, FileEntry, FileOperationError, FileReadResult
from app.models.settings import AppSettings
from app.ui.setup_wizard import SetupWizard


@dataclass(slots=True)
class SearchViewItemData:
    approved_root: str
    absolute_path: str
    relative_path: str
    is_dir: bool


class MainWindow(QMainWindow):
    def __init__(self, context: AppContext, logger: logging.Logger) -> None:
        super().__init__()
        self._context = context
        self._logger = logger
        self._settings: AppSettings = self._context.settings_store.load()
        self._pending_action: Callable[[], None] | None = None
        self._mail_results: list[MailSummary] = []
        self._current_email: MailMessageView | None = None
        self._allow_remote_images_current_message = False
        self._search_view_item_role = Qt.ItemDataRole.UserRole + 1
        self._folder_view_root = ""
        self._folder_view_relative_path = ""
        self._folder_view_history: list[str] = []

        self.setWindowTitle("Personal AI Bridge")
        self.resize(1500, 900)

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
        refresh_files_button = QPushButton("Refresh file view")
        refresh_files_button.clicked.connect(self.list_selected_folder)
        refresh_mail_button = QPushButton("Refresh Yahoo inbox")
        refresh_mail_button.clicked.connect(self.list_inbox)
        quick_layout.addWidget(setup_button)
        quick_layout.addWidget(refresh_files_button)
        quick_layout.addWidget(refresh_mail_button)

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

        self.center_tabs = QTabWidget()
        self.center_tabs.addTab(self._wrap_in_scroll_area(self._build_files_tab()), "Files")
        self.center_tabs.addTab(self._wrap_in_scroll_area(self._build_email_tab()), "Yahoo Mail")

        actions_confirm_group = QGroupBox("Pending confirmation")
        actions_confirm_layout = QVBoxLayout(actions_confirm_group)
        self.proposed_actions = QPlainTextEdit()
        self.proposed_actions.setReadOnly(True)
        self.proposed_actions.setPlainText(
            "Delete requests, move requests, overwrite requests, and send-email requests will appear here before they run."
        )
        self.confirm_button = QPushButton("Confirm action")
        self.confirm_button.setEnabled(False)
        self.confirm_button.clicked.connect(self.confirm_pending_action)
        actions_confirm_layout.addWidget(self.proposed_actions)
        actions_confirm_layout.addWidget(self.confirm_button)

        self.center_splitter = QSplitter(Qt.Vertical)
        self.center_splitter.addWidget(self.center_tabs)
        self.center_splitter.addWidget(actions_confirm_group)
        self.center_splitter.setChildrenCollapsible(False)
        self.center_splitter.setStretchFactor(0, 1)
        self.center_splitter.setStretchFactor(1, 0)

        layout.addWidget(self.center_splitter)
        return widget

    def _build_files_tab(self) -> QWidget:
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
        self.create_content_input.setMaximumHeight(120)
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
            "Choose a tab to work with files or Yahoo Mail. Mail listing and reading work without AI; summarizing and drafting need AI."
        )
        results_layout.addWidget(self.results_output)

        layout.addWidget(browser_group)
        layout.addWidget(actions_group)
        layout.addWidget(results_group)
        return widget

    def _build_email_tab(self) -> QWidget:
        widget = QWidget()
        layout = QVBoxLayout(widget)
        layout.setSpacing(14)

        settings_group = QGroupBox("Yahoo settings")
        settings_layout = QFormLayout(settings_group)
        self.yahoo_email_input = QLineEdit()
        self.yahoo_password_input = QLineEdit()
        self.yahoo_password_input.setEchoMode(QLineEdit.Password)
        self.yahoo_imap_server_input = QLineEdit()
        self.yahoo_imap_port_input = QLineEdit()
        self.yahoo_smtp_server_input = QLineEdit()
        self.yahoo_smtp_port_input = QLineEdit()
        self.yahoo_settings_message = QLabel(
            "Use a Yahoo app password. Regular Yahoo passwords are not supported for IMAP/SMTP access here."
        )
        self.yahoo_settings_message.setWordWrap(True)
        settings_layout.addRow("Yahoo email", self.yahoo_email_input)
        settings_layout.addRow("Yahoo app password", self.yahoo_password_input)
        settings_layout.addRow("IMAP server", self.yahoo_imap_server_input)
        settings_layout.addRow("IMAP port", self.yahoo_imap_port_input)
        settings_layout.addRow("SMTP server", self.yahoo_smtp_server_input)
        settings_layout.addRow("SMTP port", self.yahoo_smtp_port_input)
        settings_layout.addRow("", self.yahoo_settings_message)
        settings_buttons = QHBoxLayout()
        save_settings_button = QPushButton("Save Yahoo settings")
        save_settings_button.clicked.connect(self.save_yahoo_settings)
        test_connection_button = QPushButton("Test Yahoo connection")
        test_connection_button.clicked.connect(self.test_yahoo_connection)
        settings_buttons.addWidget(save_settings_button)
        settings_buttons.addWidget(test_connection_button)
        settings_layout.addRow("", settings_buttons)

        search_group = QGroupBox("Inbox listing and search")
        search_layout = QVBoxLayout(search_group)
        search_form = QFormLayout()
        self.mail_unread_filter = QComboBox()
        self.mail_unread_filter.addItems(["All mail", "Unread only", "Read only"])
        self.mail_sender_input = QLineEdit()
        self.mail_sender_input.setPlaceholderText("sender@example.com or part of sender name")
        self.mail_subject_input = QLineEdit()
        self.mail_subject_input.setPlaceholderText("keyword in subject")
        self.mail_start_enabled = QCheckBox("Use start date")
        self.mail_start_date = QDateEdit(QDate.currentDate().addDays(-7))
        self.mail_start_date.setCalendarPopup(True)
        self.mail_end_enabled = QCheckBox("Use end date")
        self.mail_end_date = QDateEdit(QDate.currentDate())
        self.mail_end_date.setCalendarPopup(True)
        search_form.addRow("Unread filter", self.mail_unread_filter)
        search_form.addRow("Sender", self.mail_sender_input)
        search_form.addRow("Subject keyword", self.mail_subject_input)
        search_form.addRow(self.mail_start_enabled, self.mail_start_date)
        search_form.addRow(self.mail_end_enabled, self.mail_end_date)
        search_layout.addLayout(search_form)
        search_buttons = QHBoxLayout()
        list_button = QPushButton("List inbox")
        list_button.clicked.connect(self.list_inbox)
        search_button = QPushButton("Search mail")
        search_button.clicked.connect(self.search_inbox)
        read_button = QPushButton("Read selected")
        read_button.clicked.connect(self.read_selected_email)
        summarize_button = QPushButton("Summarize selected")
        summarize_button.clicked.connect(self.summarize_selected_email)
        search_buttons.addWidget(list_button)
        search_buttons.addWidget(search_button)
        search_buttons.addWidget(read_button)
        search_buttons.addWidget(summarize_button)
        search_layout.addLayout(search_buttons)
        self.mail_results_list = QListWidget()
        self.mail_results_list.setMinimumHeight(180)
        self.mail_results_list.itemSelectionChanged.connect(self._mail_selection_changed)
        search_layout.addWidget(self.mail_results_list)

        draft_group = QGroupBox("Draft reply or new email")
        draft_layout = QVBoxLayout(draft_group)
        draft_form = QFormLayout()
        self.draft_to_input = QLineEdit()
        self.draft_subject_input = QLineEdit()
        self.draft_prompt_input = QPlainTextEdit()
        self.draft_prompt_input.setPlaceholderText(
            "Optional notes for the AI draft, such as tone, promised dates, or what you want to ask."
        )
        self.draft_prompt_input.setMaximumHeight(90)
        draft_form.addRow("To", self.draft_to_input)
        draft_form.addRow("Subject", self.draft_subject_input)
        draft_layout.addLayout(draft_form)
        draft_layout.addWidget(self.draft_prompt_input)
        draft_buttons = QHBoxLayout()
        draft_reply_button = QPushButton("Draft reply")
        draft_reply_button.clicked.connect(self.draft_reply)
        draft_new_button = QPushButton("Draft new email")
        draft_new_button.clicked.connect(self.draft_new_email)
        send_button = QPushButton("Send draft")
        send_button.clicked.connect(self.request_send_email)
        draft_buttons.addWidget(draft_reply_button)
        draft_buttons.addWidget(draft_new_button)
        draft_buttons.addWidget(send_button)
        draft_layout.addLayout(draft_buttons)
        self.draft_body_input = QPlainTextEdit()
        self.draft_body_input.setPlaceholderText("The editable draft body appears here.")
        self.draft_body_input.setMinimumHeight(130)
        draft_layout.addWidget(self.draft_body_input)

        layout.addWidget(settings_group)
        layout.addWidget(search_group)
        layout.addWidget(draft_group)
        return widget


    def _wrap_in_scroll_area(self, content: QWidget) -> QScrollArea:
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(content)
        return scroll

    def _build_preview_panel(self) -> QWidget:
        tabs = QTabWidget()
        preview_widget = QWidget()
        preview_layout = QVBoxLayout(preview_widget)
        preview_layout.setContentsMargins(6, 6, 6, 6)
        preview_layout.setSpacing(8)
        self.email_preview_notice = QLabel("Remote images are blocked by default for privacy.")
        self.email_preview_notice.setWordWrap(True)
        self.load_remote_images_button = QPushButton("Load remote images")
        self.load_remote_images_button.setEnabled(False)
        self.load_remote_images_button.clicked.connect(self._load_remote_images_for_current_message)
        preview_controls = QHBoxLayout()
        preview_controls.addWidget(self.email_preview_notice, 1)
        preview_controls.addWidget(self.load_remote_images_button, 0)
        self.email_preview = QTextBrowser()
        self.email_preview.setOpenExternalLinks(True)
        self.email_preview.setHtml("<p>Select a Yahoo email and read it to preview the message body here.</p>")
        preview_layout.addLayout(preview_controls)
        preview_layout.addWidget(self.email_preview)
        self.file_preview = QPlainTextEdit()
        self.file_preview.setReadOnly(True)
        self.file_preview.setPlainText("Selected file contents will appear here.")
        search_view_widget = QWidget()
        search_view_layout = QVBoxLayout(search_view_widget)
        self.search_context_label = QLabel("Folder listings and file search results appear below.")
        self.search_context_label.setWordWrap(True)
        navigation_layout = QHBoxLayout()
        self.search_back_button = QPushButton("Back")
        self.search_back_button.clicked.connect(self._navigate_back_folder_view)
        self.search_up_button = QPushButton("Up")
        self.search_up_button.clicked.connect(self._navigate_up_folder_view)
        self.search_root_button = QPushButton("Root")
        self.search_root_button.clicked.connect(self._navigate_root_folder_view)
        self.search_path_label = QLabel("Current folder: (not selected)")
        self.search_path_label.setWordWrap(True)
        navigation_layout.addWidget(self.search_back_button)
        navigation_layout.addWidget(self.search_up_button)
        navigation_layout.addWidget(self.search_root_button)
        navigation_layout.addWidget(self.search_path_label, 1)
        self.search_context_tree = QTreeWidget()
        self.search_context_tree.setHeaderLabels(["Type", "Relative path", "Size"])
        self.search_context_tree.setRootIsDecorated(False)
        self.search_context_tree.setAlternatingRowColors(True)
        self.search_context_tree.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.search_context_tree.itemClicked.connect(self._search_item_clicked)
        self.search_context_tree.itemDoubleClicked.connect(self._search_item_double_clicked)
        self.search_context_tree.customContextMenuRequested.connect(self._show_search_item_context_menu)
        search_view_layout.addWidget(self.search_context_label)
        search_view_layout.addLayout(navigation_layout)
        search_view_layout.addWidget(self.search_context_tree)
        tabs.addTab(preview_widget, "Email preview")
        tabs.addTab(self.file_preview, "File preview")
        tabs.addTab(search_view_widget, "Folder/search view")
        return tabs

    def refresh_ui(self) -> None:
        self._settings = self._context.settings_store.load()
        folders = self._context.folder_registry.list_folders()

        self.yahoo_status_label.setText(
            f"Yahoo Mail: {self._context.yahoo_mail_service.connection_status_text()}"
        )
        self.folders_status_label.setText(
            f"Folder access: {'Configured' if folders else 'Not configured'}"
        )
        self.ai_status_label.setText(
            f"AI: {'Configured' if self._context.ai_client.is_available(self._settings) else 'Not configured'}"
        )
        provider_label = self._settings.provider.label or "Not configured"
        model_name = self._settings.provider.model_name or "No model selected"
        self.provider_status_label.setText(f"Provider/model: {provider_label} / {model_name}")

        self.yahoo_email_input.setText(self._settings.yahoo_email)
        self.yahoo_password_input.setText(self._settings.yahoo_app_password)
        self.yahoo_imap_server_input.setText(self._settings.yahoo_imap_server)
        self.yahoo_imap_port_input.setText(str(self._settings.yahoo_imap_port))
        self.yahoo_smtp_server_input.setText(self._settings.yahoo_smtp_server)
        self.yahoo_smtp_port_input.setText(str(self._settings.yahoo_smtp_port))

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
        self._validate_folder_navigation_state()
        self._update_folder_navigation_controls()

        self.recent_commands.clear()
        for entry in self._context.action_logger.recent_entries(limit=10):
            line = f"{entry['timestamp']} — {entry['action_type']} — {entry['status']} — {entry['target']}"
            self.recent_commands.addItem(line)

    def run_setup_wizard(self) -> None:
        wizard = SetupWizard(
            self._settings,
            self._context.folder_registry.list_folders(),
            self._context.yahoo_mail_service,
            self,
        )
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

    def save_yahoo_settings(self) -> None:
        try:
            settings = self._settings_with_current_yahoo_fields()
            self._context.settings_store.save(settings)
            self.yahoo_settings_message.setText(
                "Yahoo settings saved. Use Test Yahoo connection to confirm the app password works."
            )
            self._context.action_logger.record("email_settings_save", settings.yahoo_email, "success")
            self._show_result("Yahoo settings saved.")
        except ValueError as exc:
            self._show_error("Could not save Yahoo settings", exc)

    def test_yahoo_connection(self) -> None:
        try:
            settings = self._settings_with_current_yahoo_fields()
            self._context.settings_store.save(settings)
            result = self._context.yahoo_mail_service.test_connection(settings)
            self.yahoo_settings_message.setText(result.message)
            self._show_result(result.message)
        except (ValueError, YahooMailError) as exc:
            self._show_error("Yahoo connection failed", exc)

    def list_inbox(self) -> None:
        self._run_mail_search(allow_filters=False)

    def search_inbox(self) -> None:
        self._run_mail_search(allow_filters=True)

    def _run_mail_search(self, allow_filters: bool) -> None:
        try:
            results = self._context.yahoo_mail_service.list_inbox(
                unread_only=self._selected_unread_filter() if allow_filters else None,
                sender=self.mail_sender_input.text() if allow_filters else "",
                subject_keyword=self.mail_subject_input.text() if allow_filters else "",
                start_date=self._selected_start_date() if allow_filters else None,
                end_date=self._selected_end_date() if allow_filters else None,
            )
            self._mail_results = results
            self._populate_mail_results(results)
            summary = f"Loaded {len(results)} Yahoo message(s)."
            if not results:
                summary += " Try broader search terms or remove the filters."
            self._show_result(summary)
        except YahooMailError as exc:
            self._show_error("Could not load Yahoo inbox", exc)

    def read_selected_email(self) -> None:
        selected = self._selected_mail_summary()
        if not selected:
            self._show_result("Select a Yahoo message from the results list first.")
            return
        self._load_mail_preview(selected, trigger="manual")

    def _load_mail_preview(self, selected: MailSummary, trigger: str) -> None:
        if self._current_email and self._current_email.uid == selected.uid:
            self._show_email(self._current_email)
            if trigger == "selection":
                self._show_result(f"Previewing Yahoo email: {self._current_email.subject}")
            else:
                self._show_result(f"Yahoo email already loaded: {self._current_email.subject}")
            return
        try:
            self._current_email = self._context.yahoo_mail_service.read_email(selected.uid)
            self._allow_remote_images_current_message = False
            self.load_remote_images_button.setEnabled(bool(self._current_email.body_html))
            self._show_email(self._current_email)
            action = "Previewing" if trigger == "selection" else "Read"
            self._show_result(f"{action} Yahoo email: {self._current_email.subject}")
        except YahooMailError as exc:
            self._show_error("Could not read Yahoo email", exc)

    def summarize_selected_email(self) -> None:
        selected = self._selected_mail_summary()
        if not selected:
            self._show_result("Select a Yahoo message before asking for a summary.")
            return
        try:
            summary = self._context.yahoo_mail_service.summarize_email(selected.uid)
            self.results_output.setPlainText(summary)
            self.refresh_ui()
        except YahooMailError as exc:
            self._show_error("Could not summarize Yahoo email", exc)

    def draft_reply(self) -> None:
        selected = self._selected_mail_summary()
        if not selected:
            self._show_result("Select a Yahoo message before drafting a reply.")
            return
        try:
            draft = self._context.yahoo_mail_service.draft_reply(
                selected.uid,
                self.draft_prompt_input.toPlainText(),
            )
            self._apply_draft(draft)
            self._show_result("Draft reply created. Review and edit it before sending.")
        except YahooMailError as exc:
            self._show_error("Could not draft reply", exc)

    def draft_new_email(self) -> None:
        try:
            draft = self._context.yahoo_mail_service.draft_new_email(
                self.draft_to_input.text(),
                self.draft_subject_input.text(),
                self.draft_prompt_input.toPlainText(),
            )
            self._apply_draft(draft)
            self._show_result("New email draft created. Review and edit it before sending.")
        except YahooMailError as exc:
            self._show_error("Could not draft new email", exc)

    def request_send_email(self) -> None:
        draft = self._current_draft()
        try:
            if not draft.to_address.strip():
                raise YahooMailError("Enter a recipient email address before sending.")
            if not draft.subject.strip():
                raise YahooMailError("Enter a subject before sending.")
            if not draft.body.strip():
                raise YahooMailError("Write or generate the email body before sending.")
        except YahooMailError as exc:
            self._show_error("Could not prepare send", exc)
            return

        self._set_pending_action(
            message=(
                "Send email requested. Confirm before the app sends anything through Yahoo SMTP.\n\n"
                f"From: {self._settings.yahoo_email or '(not configured)'}\n"
                f"To: {draft.to_address}\n"
                f"Subject: {draft.subject}\n\n"
                "The editable draft body is in the Yahoo Mail tab."
            ),
            action=self._perform_send_email,
        )

    def _perform_send_email(self) -> None:
        try:
            draft = self._current_draft()
            self._context.yahoo_mail_service.send_email(draft)
            self._show_result("Yahoo SMTP send succeeded.")
        except YahooMailError as exc:
            self._show_error("Could not send email", exc)

    def _apply_draft(self, draft: OutgoingDraft) -> None:
        self.draft_to_input.setText(draft.to_address)
        self.draft_subject_input.setText(draft.subject)
        self.draft_body_input.setPlainText(draft.body)

    def _current_draft(self) -> OutgoingDraft:
        return OutgoingDraft(
            to_address=self.draft_to_input.text().strip(),
            subject=self.draft_subject_input.text().strip(),
            body=self.draft_body_input.toPlainText().strip(),
        )

    def _populate_mail_results(self, results: list[MailSummary]) -> None:
        self.mail_results_list.clear()
        for message in results:
            unread_prefix = "[Unread] " if message.unread else "[Read] "
            label = f"{unread_prefix}{message.received_at} — {message.sender} — {message.subject}"
            item = QListWidgetItem(label)
            item.setData(Qt.ItemDataRole.UserRole, message.uid)
            self.mail_results_list.addItem(item)
        if not results:
            self.email_preview.setHtml("<p>No Yahoo messages matched the current filters.</p>")

    def _selected_mail_summary(self) -> MailSummary | None:
        item = self.mail_results_list.currentItem()
        if not item:
            return None
        uid = item.data(Qt.ItemDataRole.UserRole)
        return next((message for message in self._mail_results if message.uid == uid), None)

    def _mail_selection_changed(self) -> None:
        selected = self._selected_mail_summary()
        if not selected:
            return
        self._load_mail_preview(selected, trigger="selection")

    def _show_email(self, message: MailMessageView) -> None:
        preview_html = self._context.yahoo_mail_service.build_safe_preview_html(
            message,
            allow_remote_images=self._allow_remote_images_current_message,
        )
        self.email_preview.setHtml(preview_html)
        attachments = (
            ", ".join(f"{item.filename} ({item.size} bytes)" for item in message.attachments)
            if message.attachments
            else "none"
        )
        self.email_preview_notice.setText(
            "Inline images: "
            f"{len(message.inline_images)} | Attachments: {attachments}"
            if self._allow_remote_images_current_message
            else "Remote images are blocked by default for privacy. "
            f"Inline images: {len(message.inline_images)} | Attachments: {attachments}"
        )
        self.draft_to_input.setText(self.draft_to_input.text().strip() or self._extract_reply_target(message.sender))
        if not self.draft_subject_input.text().strip():
            subject = message.subject if message.subject.lower().startswith("re:") else f"Re: {message.subject}"
            self.draft_subject_input.setText(subject)

    def _load_remote_images_for_current_message(self) -> None:
        if not self._current_email:
            return
        self._allow_remote_images_current_message = True
        self._show_email(self._current_email)

    def _extract_reply_target(self, sender: str) -> str:
        if "<" in sender and ">" in sender:
            return sender.split("<", 1)[1].split(">", 1)[0].strip()
        return sender.strip()

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
            self._show_file_search_results(root, matches)
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
            self.refresh_ui()
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

    def _settings_with_current_yahoo_fields(self) -> AppSettings:
        imap_port = int(self.yahoo_imap_port_input.text().strip())
        smtp_port = int(self.yahoo_smtp_port_input.text().strip())
        settings = self._context.settings_store.load()
        settings.yahoo_email = self.yahoo_email_input.text().strip()
        settings.yahoo_app_password = self.yahoo_password_input.text()
        settings.yahoo_imap_server = self.yahoo_imap_server_input.text().strip()
        settings.yahoo_imap_port = imap_port
        settings.yahoo_smtp_server = self.yahoo_smtp_server_input.text().strip()
        settings.yahoo_smtp_port = smtp_port
        return settings

    def _selected_unread_filter(self) -> bool | None:
        index = self.mail_unread_filter.currentIndex()
        if index == 1:
            return True
        if index == 2:
            return False
        return None

    def _selected_start_date(self) -> date | None:
        if not self.mail_start_enabled.isChecked():
            return None
        value = self.mail_start_date.date()
        return date(value.year(), value.month(), value.day())

    def _selected_end_date(self) -> date | None:
        if not self.mail_end_enabled.isChecked():
            return None
        value = self.mail_end_date.date()
        return date(value.year(), value.month(), value.day())

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
        self.search_context_label.setText(f"Folder listing for {listing.root}:{listing.relative_path or '.'}")
        self._folder_view_root = listing.root
        self._folder_view_relative_path = listing.relative_path
        self._update_folder_navigation_controls()
        self.search_context_tree.clear()
        for entry in listing.entries:
            self._append_search_item(
                approved_root=listing.root,
                absolute_path=entry.path,
                relative_path=entry.relative_path,
                is_dir=entry.is_dir,
                size=entry.size,
            )
        if not listing.entries:
            empty_item = QTreeWidgetItem(["—", "(empty folder)", ""])
            empty_item.setFlags(empty_item.flags() & ~Qt.ItemFlag.ItemIsSelectable)
            self.search_context_tree.addTopLevelItem(empty_item)

    def _show_file_search_results(self, root: str, matches: list[FileEntry]) -> None:
        self.search_context_label.setText(f"Search results in {root}")
        self.search_context_tree.clear()
        for entry in matches:
            self._append_search_item(
                approved_root=root,
                absolute_path=entry.path,
                relative_path=entry.relative_path,
                is_dir=entry.is_dir,
                size=entry.size,
            )
        if not matches:
            empty_item = QTreeWidgetItem(["—", "(no matching files or folders)", ""])
            empty_item.setFlags(empty_item.flags() & ~Qt.ItemFlag.ItemIsSelectable)
            self.search_context_tree.addTopLevelItem(empty_item)

    def _append_search_item(
        self,
        approved_root: str,
        absolute_path: str,
        relative_path: str,
        is_dir: bool,
        size: int,
    ) -> None:
        item_type = "Folder" if is_dir else "File"
        size_text = "" if is_dir else f"{size} bytes"
        item = QTreeWidgetItem([item_type, relative_path, size_text])
        item_data = SearchViewItemData(
            approved_root=approved_root,
            absolute_path=absolute_path,
            relative_path=relative_path,
            is_dir=is_dir,
        )
        item.setData(0, self._search_view_item_role, item_data)
        self.search_context_tree.addTopLevelItem(item)

    def _search_item_clicked(self, item: QTreeWidgetItem, _column: int) -> None:
        item_data = self._search_item_data(item)
        if not item_data:
            return
        self.path_input.setText(item_data.relative_path)
        self._restore_combo_value(self.root_selector, item_data.approved_root)
        if item_data.is_dir:
            self._navigate_to_folder(
                root=item_data.approved_root,
                relative_path=item_data.relative_path,
                push_history=True,
            )
            return
        try:
            read_result = self._context.file_service.read_file(item_data.approved_root, item_data.relative_path)
            self._show_file(read_result)
            self._show_result(f"Selected file: {item_data.relative_path}")
        except FileOperationError:
            self._show_result(f"Selected file path: {item_data.relative_path}")

    def _search_item_double_clicked(self, item: QTreeWidgetItem, _column: int) -> None:
        item_data = self._search_item_data(item)
        if not item_data:
            return
        if item_data.is_dir:
            self._navigate_to_folder(
                root=item_data.approved_root,
                relative_path=item_data.relative_path,
                push_history=True,
            )
        else:
            self.path_input.setText(item_data.relative_path)
            self.read_selected_file()

    def _show_search_item_context_menu(self, position: QPoint) -> None:
        item = self.search_context_tree.itemAt(position)
        item_data = self._search_item_data(item) if item else None
        if not item_data:
            return
        menu = QMenu(self)
        open_action = menu.addAction("Open containing folder")
        copy_relative_action = menu.addAction("Copy relative path")
        copy_full_action = menu.addAction("Copy full path")
        chosen = menu.exec(self.search_context_tree.viewport().mapToGlobal(position))
        if chosen == open_action:
            self._open_item_location(item_data)
        elif chosen == copy_relative_action:
            QApplication.clipboard().setText(item_data.relative_path)
            self._show_result(f"Copied relative path: {item_data.relative_path}")
        elif chosen == copy_full_action:
            QApplication.clipboard().setText(item_data.absolute_path)
            self._show_result(f"Copied full path: {item_data.absolute_path}")

    def _search_item_data(self, item: QTreeWidgetItem | None) -> SearchViewItemData | None:
        if not item:
            return None
        value = item.data(0, self._search_view_item_role)
        if isinstance(value, SearchViewItemData):
            return value
        return None

    def _open_item_location(self, item_data: SearchViewItemData) -> None:
        target_path = Path(item_data.absolute_path) if item_data.is_dir else Path(item_data.absolute_path).parent
        try:
            if os.name == "nt":
                os.startfile(str(target_path))
            elif sys.platform == "darwin":
                subprocess.Popen(["open", str(target_path)])
            else:
                subprocess.Popen(["xdg-open", str(target_path)])
            self._show_result(f"Opened location: {target_path}")
        except Exception as exc:
            self._show_error("Could not open path in file explorer", exc)

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
            "Delete requests, move requests, overwrite requests, and send-email requests will appear here before they run."
        )
        self.confirm_button.setEnabled(False)

    def _root_changed(self) -> None:
        if not self.destination_root_selector.currentText().strip() and self.root_selector.count() > 0:
            self.destination_root_selector.setCurrentIndex(self.root_selector.currentIndex())
        selected_root = self.root_selector.currentText().strip()
        if not selected_root:
            self._reset_folder_navigation_state()
            self._update_folder_navigation_controls()
            return
        self._folder_view_root = selected_root
        self._folder_view_relative_path = ""
        self._folder_view_history.clear()
        self.path_input.setText("")
        self.list_selected_folder()

    def _navigate_to_folder(self, root: str, relative_path: str, push_history: bool) -> None:
        previous_root = self._folder_view_root
        previous_relative_path = self._folder_view_relative_path
        if push_history and previous_root == root:
            self._folder_view_history.append(previous_relative_path)
        if previous_root and previous_root != root:
            self._folder_view_history.clear()
        self._restore_combo_value(self.root_selector, root)
        self.path_input.setText(relative_path)
        self.list_selected_folder()

    def _navigate_back_folder_view(self) -> None:
        if not self._folder_view_history:
            return
        previous_relative_path = self._folder_view_history.pop()
        root = self._folder_view_root or self._selected_root()
        if not root:
            return
        self._navigate_to_folder(root=root, relative_path=previous_relative_path, push_history=False)
        self._update_folder_navigation_controls()

    def _navigate_up_folder_view(self) -> None:
        root = self._folder_view_root or self._selected_root()
        if not root:
            return
        if not self._folder_view_relative_path:
            self._update_folder_navigation_controls()
            return
        parent = Path(self._folder_view_relative_path).parent.as_posix()
        if parent == ".":
            parent = ""
        self._navigate_to_folder(root=root, relative_path=parent, push_history=True)

    def _navigate_root_folder_view(self) -> None:
        root = self._selected_root()
        if not root:
            return
        self._folder_view_root = root
        self._folder_view_relative_path = ""
        self._folder_view_history.clear()
        self.path_input.setText("")
        self.list_selected_folder()

    def _reset_folder_navigation_state(self) -> None:
        self._folder_view_root = ""
        self._folder_view_relative_path = ""
        self._folder_view_history.clear()
        self.search_path_label.setText("Current folder: (not selected)")

    def _validate_folder_navigation_state(self) -> None:
        current_roots = {self.root_selector.itemText(index) for index in range(self.root_selector.count())}
        if self._folder_view_root and self._folder_view_root not in current_roots:
            self._reset_folder_navigation_state()

    def _update_folder_navigation_controls(self) -> None:
        at_root = not self._folder_view_relative_path
        has_back = bool(self._folder_view_history)
        has_root = bool(self._folder_view_root)
        self.search_back_button.setEnabled(has_back)
        self.search_up_button.setEnabled(has_root and not at_root)
        self.search_root_button.setEnabled(has_root)
        if not has_root:
            self.search_path_label.setText("Current folder: (not selected)")
            return
        display_relative = self._folder_view_relative_path or "."
        self.search_path_label.setText(f"Current folder: {self._folder_view_root}:{display_relative}")
