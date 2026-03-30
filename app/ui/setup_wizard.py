from __future__ import annotations

from pathlib import Path

from PySide6.QtWidgets import (
    QButtonGroup,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QRadioButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
    QWizard,
    QWizardPage,
)

from app.ai.client import AIClient
from app.ai.providers import PROVIDER_OPTIONS, get_provider_option
from app.email.yahoo_service import YahooMailError, YahooMailService
from app.models.settings import AppSettings, ProviderConfig


class WelcomePage(QWizardPage):
    def __init__(self) -> None:
        super().__init__()
        self.setTitle("Welcome")
        self.setSubTitle("Personal AI Bridge will help you set up folders, Yahoo Mail, and optional AI.")
        layout = QVBoxLayout()
        layout.addWidget(
            QLabel(
                "Use a Yahoo app password for mail access. Regular Yahoo account passwords will not work here."
            )
        )
        self.setLayout(layout)


class AiModePage(QWizardPage):
    def __init__(self, initial_mode: str) -> None:
        super().__init__()
        self.setTitle("Choose AI mode")
        self.setSubTitle("You can use a local model, a cloud/API endpoint, or skip AI for now.")
        layout = QVBoxLayout()
        self._buttons = QButtonGroup(self)
        for index, (label, mode) in enumerate(
            [("Local AI", "local"), ("Cloud/API AI", "cloud"), ("Skip for now", "skip")]
        ):
            button = QRadioButton(label)
            self._buttons.addButton(button, index)
            button.setProperty("mode", mode)
            layout.addWidget(button)
            if mode == initial_mode:
                button.setChecked(True)
        if not any(button.isChecked() for button in self._buttons.buttons()):
            self._buttons.buttons()[-1].setChecked(True)
        self.setLayout(layout)

    def selected_mode(self) -> str:
        checked = next((button for button in self._buttons.buttons() if button.isChecked()), None)
        return checked.property("mode") if checked else "skip"


class ProviderPage(QWizardPage):
    def __init__(
        self,
        initial_provider: ProviderConfig,
        initial_provider_timeout_local: int,
        initial_provider_timeout_cloud: int,
    ) -> None:
        super().__init__()
        self.setTitle("AI provider")
        self.setSubTitle("Choose a provider type and enter connection details.")

        layout = QFormLayout()

        self.provider_list = QListWidget()
        for option in PROVIDER_OPTIONS:
            item = QListWidgetItem(f"{option.label} — {option.description}")
            item.setData(1, option.key)
            self.provider_list.addItem(item)
            if option.key == initial_provider.provider_type:
                self.provider_list.setCurrentItem(item)
        if self.provider_list.count() and self.provider_list.currentRow() == -1:
            self.provider_list.setCurrentRow(0)

        self.base_url_edit = QLineEdit(initial_provider.base_url)
        self.model_edit = QLineEdit(initial_provider.model_name)
        self.api_key_edit = QLineEdit(initial_provider.api_key)
        self.api_key_edit.setEchoMode(QLineEdit.Password)
        self.local_timeout_edit = QSpinBox()
        self.local_timeout_edit.setRange(10, 1200)
        self.local_timeout_edit.setValue(max(10, initial_provider_timeout_local))
        self.cloud_timeout_edit = QSpinBox()
        self.cloud_timeout_edit.setRange(10, 600)
        self.cloud_timeout_edit.setValue(max(10, initial_provider_timeout_cloud))

        self.provider_list.currentItemChanged.connect(self._apply_provider_defaults)

        layout.addRow("Provider", self.provider_list)
        layout.addRow("Base URL", self.base_url_edit)
        layout.addRow("Model", self.model_edit)
        layout.addRow("API key", self.api_key_edit)
        layout.addRow("Local AI timeout (seconds)", self.local_timeout_edit)
        layout.addRow("Cloud AI timeout (seconds)", self.cloud_timeout_edit)
        self.setLayout(layout)
        self._apply_provider_defaults()

    def _apply_provider_defaults(self) -> None:
        item = self.provider_list.currentItem()
        if not item:
            return
        option = get_provider_option(item.data(1))
        if option and not self.base_url_edit.text().strip():
            self.base_url_edit.setText(option.default_base_url)

    def selected_provider(self) -> ProviderConfig:
        item = self.provider_list.currentItem()
        provider_key = item.data(1) if item else "none"
        option = get_provider_option(provider_key)
        label = option.label if option else "Not configured"
        local_only = option.local_only if option else True
        return ProviderConfig(
            provider_type=provider_key,
            label=label,
            base_url=self.base_url_edit.text().strip(),
            model_name=self.model_edit.text().strip(),
            api_key=self.api_key_edit.text(),
            local_only=local_only,
        )


class YahooPage(QWizardPage):
    def __init__(self, settings: AppSettings) -> None:
        super().__init__()
        self.setTitle("Yahoo Mail")
        self.setSubTitle("Enter your Yahoo email address and Yahoo app password.")
        layout = QFormLayout()
        self.email_edit = QLineEdit(settings.yahoo_email)
        self.password_edit = QLineEdit(settings.yahoo_app_password)
        self.password_edit.setEchoMode(QLineEdit.Password)
        self.imap_server_edit = QLineEdit(settings.yahoo_imap_server)
        self.imap_port_edit = QSpinBox()
        self.imap_port_edit.setRange(1, 65535)
        self.imap_port_edit.setValue(settings.yahoo_imap_port)
        self.smtp_server_edit = QLineEdit(settings.yahoo_smtp_server)
        self.smtp_port_edit = QSpinBox()
        self.smtp_port_edit.setRange(1, 65535)
        self.smtp_port_edit.setValue(settings.yahoo_smtp_port)
        help_label = QLabel(
            "Yahoo expects an app password here. Generate it in your Yahoo account security settings."
        )
        help_label.setWordWrap(True)
        layout.addRow("Yahoo email", self.email_edit)
        layout.addRow("App password", self.password_edit)
        layout.addRow("IMAP server", self.imap_server_edit)
        layout.addRow("IMAP port", self.imap_port_edit)
        layout.addRow("SMTP server", self.smtp_server_edit)
        layout.addRow("SMTP port", self.smtp_port_edit)
        layout.addRow("", help_label)
        self.setLayout(layout)


class FoldersPage(QWizardPage):
    def __init__(self, initial_folders: list[str]) -> None:
        super().__init__()
        self.setTitle("Approved folders")
        self.setSubTitle("Choose folders the app is allowed to access.")
        layout = QVBoxLayout()
        self.folder_list = QListWidget()
        for folder in initial_folders:
            self.folder_list.addItem(folder)

        button_row = QHBoxLayout()
        add_button = QPushButton("Add folder")
        remove_button = QPushButton("Remove selected")
        add_button.clicked.connect(self._add_folder)
        remove_button.clicked.connect(self._remove_selected)
        button_row.addWidget(add_button)
        button_row.addWidget(remove_button)

        layout.addWidget(self.folder_list)
        layout.addLayout(button_row)
        self.setLayout(layout)

    def _add_folder(self) -> None:
        folder = QFileDialog.getExistingDirectory(self, "Choose approved folder")
        if folder and not self._contains(folder):
            self.folder_list.addItem(folder)

    def _remove_selected(self) -> None:
        row = self.folder_list.currentRow()
        if row >= 0:
            self.folder_list.takeItem(row)

    def _contains(self, folder: str) -> bool:
        return any(self.folder_list.item(index).text() == folder for index in range(self.folder_list.count()))

    def folders(self) -> list[str]:
        return [self.folder_list.item(index).text() for index in range(self.folder_list.count())]


class TestConnectionsPage(QWizardPage):
    def __init__(
        self,
        settings_builder,
        yahoo_mail_service: YahooMailService,
        ai_client: AIClient,
    ) -> None:
        super().__init__()
        self._settings_builder = settings_builder
        self._yahoo_mail_service = yahoo_mail_service
        self._ai_client = ai_client
        self.setTitle("Test connections")
        self.setSubTitle("Test Yahoo Mail now. You can skip AI for now and still use mail listing and reading.")
        layout = QVBoxLayout()
        self.status_label = QLabel(
            "Yahoo Mail test has not been run yet. Use the Yahoo app password above, then click Test Yahoo connection."
        )
        self.status_label.setWordWrap(True)
        test_button = QPushButton("Test Yahoo connection")
        test_button.clicked.connect(self._test_yahoo)
        layout.addWidget(self.status_label)
        layout.addWidget(test_button)
        test_ai_button = QPushButton("Test AI provider")
        test_ai_button.clicked.connect(self._test_ai)
        layout.addWidget(test_ai_button)
        self.setLayout(layout)

    def _test_yahoo(self) -> None:
        try:
            result = self._yahoo_mail_service.test_connection(self._settings_builder())
            self.status_label.setText(result.message)
            QMessageBox.information(self, "Yahoo connection works", result.message)
        except YahooMailError as exc:
            self.status_label.setText(str(exc))
            QMessageBox.warning(self, "Yahoo connection failed", str(exc))

    def _test_ai(self) -> None:
        result = self._ai_client.test_provider(self._settings_builder())
        details = (
            f"Provider: {result.provider}\n"
            f"Model: {result.model}\n"
            f"Elapsed: {result.elapsed_seconds:.1f}s\n"
            f"Result: {'Success' if result.success else 'Failed'}\n\n"
            f"{result.message}"
        )
        self.status_label.setText(details)
        if result.success:
            QMessageBox.information(self, "AI provider works", details)
        else:
            QMessageBox.warning(self, "AI provider test failed", details)


class FinishPage(QWizardPage):
    def __init__(self) -> None:
        super().__init__()
        self.setTitle("Finish")
        self.setSubTitle("Finish to save your settings and return to the main window.")
        layout = QVBoxLayout()
        layout.addWidget(QLabel("You can reopen this wizard later from the main window."))
        self.setLayout(layout)


class SetupWizard(QWizard):
    def __init__(
        self,
        settings: AppSettings,
        allowed_folders: list[str],
        yahoo_mail_service: YahooMailService,
        ai_client: AIClient,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._initial_execution_policy = settings.execution_policy or "confirm_destructive_external"
        self.setWindowTitle("Personal AI Bridge Setup")
        self.setWizardStyle(QWizard.ModernStyle)
        self.resize(760, 560)

        initial_mode = settings.ai_mode if settings.ai_mode in {"local", "cloud", "skip"} else "skip"
        self.welcome_page = WelcomePage()
        self.ai_mode_page = AiModePage(initial_mode)
        self.provider_page = ProviderPage(
            settings.provider,
            initial_provider_timeout_local=settings.ai_local_timeout_seconds,
            initial_provider_timeout_cloud=settings.ai_cloud_timeout_seconds,
        )
        self.yahoo_page = YahooPage(settings)
        self.folders_page = FoldersPage(allowed_folders)
        self.test_page = TestConnectionsPage(self.build_settings, yahoo_mail_service, ai_client)
        self.finish_page = FinishPage()

        self.addPage(self.welcome_page)
        self.addPage(self.ai_mode_page)
        self.addPage(self.provider_page)
        self.addPage(self.yahoo_page)
        self.addPage(self.folders_page)
        self.addPage(self.test_page)
        self.addPage(self.finish_page)

    def build_settings(self) -> AppSettings:
        provider = self.provider_page.selected_provider()
        ai_mode = self.ai_mode_page.selected_mode()
        if ai_mode == "skip":
            provider = ProviderConfig()
        return AppSettings(
            ai_mode=ai_mode,
            provider=provider,
            yahoo_email=self.yahoo_page.email_edit.text().strip(),
            yahoo_app_password=self.yahoo_page.password_edit.text(),
            yahoo_imap_server=self.yahoo_page.imap_server_edit.text().strip(),
            yahoo_imap_port=self.yahoo_page.imap_port_edit.value(),
            yahoo_smtp_server=self.yahoo_page.smtp_server_edit.text().strip(),
            yahoo_smtp_port=self.yahoo_page.smtp_port_edit.value(),
            ai_local_timeout_seconds=self.provider_page.local_timeout_edit.value(),
            ai_cloud_timeout_seconds=self.provider_page.cloud_timeout_edit.value(),
            execution_policy=self._initial_execution_policy,
            setup_complete=True,
        )

    def selected_folders(self) -> list[str]:
        normalized: list[str] = []
        for folder in self.folders_page.folders():
            normalized.append(str(Path(folder).expanduser().resolve()))
        return normalized

    def show_saved_message(self) -> None:
        QMessageBox.information(self, "Saved", "Setup values were saved successfully.")
