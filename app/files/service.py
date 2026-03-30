from __future__ import annotations

import csv
import json
import os
import subprocess
import shutil
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from app.ai.client import AIClient, AIUnavailableError
from app.data.action_log import ActionLogger
from app.data.database import get_app_data_dir
from app.data.settings_store import SettingsStore
from app.files.folder_registry import AllowedFolderRegistry
from app.security.path_guard import PathAccessError, PathGuard

SUPPORTED_TEXT_EXTENSIONS = {".txt", ".md", ".csv", ".json", ".doc", ".docx", ".pdf"}
CREATABLE_TEXT_EXTENSIONS = {".txt", ".md", ".csv", ".json"}


class FileOperationError(RuntimeError):
    """Raised for user-facing file operation failures."""


class UnsupportedFileTypeError(FileOperationError):
    """Raised when the user requests an unsupported file type."""


@dataclass(slots=True)
class FileEntry:
    name: str
    path: str
    relative_path: str
    is_dir: bool
    size: int


@dataclass(slots=True)
class DirectoryListing:
    root: str
    relative_path: str
    entries: list[FileEntry]


@dataclass(slots=True)
class FileReadResult:
    path: str
    relative_path: str
    content: str


class FileService:
    def __init__(
        self,
        folder_registry: AllowedFolderRegistry,
        action_logger: ActionLogger,
        settings_store: SettingsStore,
        ai_client: AIClient,
    ) -> None:
        self._folder_registry = folder_registry
        self._action_logger = action_logger
        self._settings_store = settings_store
        self._ai_client = ai_client
        self._trash_dir = get_app_data_dir() / "safe_trash"
        self._trash_dir.mkdir(parents=True, exist_ok=True)

    def list_allowed_roots(self) -> list[str]:
        return self._folder_registry.list_folders()

    def add_allowed_root(self, folder_path: str) -> str:
        normalized = self._folder_registry.add_folder(folder_path)
        self._record("approved_root_add", normalized, "success")
        return normalized

    def remove_allowed_root(self, folder_path: str) -> str:
        normalized = str(PathGuard.normalize(folder_path))
        self._folder_registry.remove_folder(normalized)
        self._record("approved_root_remove", normalized, "success")
        return normalized

    def list_directory(self, root: str, relative_path: str = "") -> DirectoryListing:
        action_target = f"{root}:{relative_path or '.'}"
        try:
            root_path = self._require_root(root)
            directory = PathGuard.resolve_relative_path(root_path, relative_path)
            if not directory.exists():
                raise FileNotFoundError(f"Folder does not exist: {directory}")
            if not directory.is_dir():
                raise NotADirectoryError(f"Path is not a folder: {directory}")
            entries = [
                FileEntry(
                    name=item.name,
                    path=str(item),
                    relative_path=self._to_relative(root_path, item),
                    is_dir=item.is_dir(),
                    size=item.stat().st_size if item.is_file() else 0,
                )
                for item in sorted(
                    directory.iterdir(),
                    key=lambda value: (not value.is_dir(), value.name.lower()),
                )
            ]
            listing = DirectoryListing(
                root=str(root_path),
                relative_path=self._to_relative(root_path, directory),
                entries=entries,
            )
            self._record("file_list", action_target, "success")
            return listing
        except Exception as exc:
            self._record("file_list", action_target, "error", str(exc))
            raise self._user_error(exc) from exc

    def search_files(self, root: str, query: str) -> list[FileEntry]:
        cleaned_query = query.strip().lower()
        action_target = f"{root}:{query}"
        try:
            if not cleaned_query:
                raise FileOperationError("Enter part of a file or folder name to search.")
            root_path = self._require_root(root)
            matches: list[FileEntry] = []
            for item in root_path.rglob("*"):
                if cleaned_query not in item.name.lower():
                    continue
                matches.append(
                    FileEntry(
                        name=item.name,
                        path=str(item),
                        relative_path=self._to_relative(root_path, item),
                        is_dir=item.is_dir(),
                        size=item.stat().st_size if item.is_file() else 0,
                    )
                )
            matches.sort(key=lambda entry: (not entry.is_dir, entry.relative_path.lower()))
            self._record("file_search", action_target, "success")
            return matches
        except Exception as exc:
            self._record("file_search", action_target, "error", str(exc))
            raise self._user_error(exc) from exc

    def read_file(self, root: str, relative_path: str) -> FileReadResult:
        action_target = f"{root}:{relative_path}"
        try:
            file_path = self._require_file(root, relative_path)
            content = self._read_supported_file(file_path)
            result = FileReadResult(
                path=str(file_path),
                relative_path=self._to_relative(self._require_root(root), file_path),
                content=content,
            )
            self._record("file_read", action_target, "success")
            return result
        except Exception as exc:
            self._record("file_read", action_target, "error", str(exc))
            raise self._user_error(exc) from exc

    def summarize_file(
        self,
        root: str,
        relative_path: str,
        on_status=None,
        on_partial=None,
        is_cancelled=None,
    ) -> str:
        action_target = f"{root}:{relative_path}"
        try:
            read_result = self.read_file(root, relative_path)
            summary = self._ai_client.summarize_text(
                read_result.content,
                self._settings_store.load(),
                on_status=on_status,
                on_partial=on_partial,
                is_cancelled=is_cancelled,
            )
            self._record("file_summary", action_target, "success")
            return summary
        except AIUnavailableError as exc:
            self._record("file_summary", action_target, "error", str(exc))
            raise FileOperationError(str(exc)) from exc
        except Exception as exc:
            self._record("file_summary", action_target, "error", str(exc))
            raise self._user_error(exc) from exc

    def create_file(self, root: str, relative_path: str, content: str = "") -> str:
        action_target = f"{root}:{relative_path}"
        try:
            destination = self._resolve_destination(root, relative_path)
            self._ensure_supported_extension(destination)
            self._ensure_creatable_extension(destination)
            if destination.exists():
                raise FileExistsError(f"File already exists: {destination}")
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_text(content, encoding="utf-8")
            self._record("file_create", action_target, "success")
            return str(destination)
        except Exception as exc:
            self._record("file_create", action_target, "error", str(exc))
            raise self._user_error(exc) from exc

    def rename_file(self, root: str, relative_path: str, new_name: str) -> str:
        action_target = f"{root}:{relative_path}->{new_name}"
        try:
            source = self._require_file(root, relative_path)
            clean_name = new_name.strip()
            if not clean_name or clean_name in {".", ".."}:
                raise FileOperationError("Enter a valid new file name.")
            if "/" in clean_name or "\\" in clean_name:
                raise FileOperationError("New file name cannot include folder separators.")
            destination = source.with_name(clean_name)
            PathGuard.ensure_within_roots(destination, self.list_allowed_roots())
            self._ensure_supported_extension(destination)
            self._ensure_creatable_extension(destination)
            if destination.exists():
                raise FileExistsError(f"A file with that name already exists: {destination}")
            source.rename(destination)
            self._record("file_rename", action_target, "success")
            return str(destination)
        except Exception as exc:
            self._record("file_rename", action_target, "error", str(exc))
            raise self._user_error(exc) from exc

    def copy_file(self, source_root: str, source_relative_path: str, destination_root: str, destination_relative_path: str, overwrite: bool = False) -> str:
        action_target = (
            f"{source_root}:{source_relative_path}->{destination_root}:{destination_relative_path}"
        )
        try:
            source = self._require_file(source_root, source_relative_path)
            destination = self._resolve_destination(destination_root, destination_relative_path)
            self._ensure_supported_extension(destination)
            self._handle_existing_destination(destination, overwrite)
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, destination)
            self._record("file_copy", action_target, "success")
            return str(destination)
        except Exception as exc:
            self._record("file_copy", action_target, "error", str(exc))
            raise self._user_error(exc) from exc

    def move_file(self, source_root: str, source_relative_path: str, destination_root: str, destination_relative_path: str, overwrite: bool = False) -> str:
        action_target = (
            f"{source_root}:{source_relative_path}->{destination_root}:{destination_relative_path}"
        )
        try:
            source = self._require_file(source_root, source_relative_path)
            destination = self._resolve_destination(destination_root, destination_relative_path)
            self._ensure_supported_extension(destination)
            self._handle_existing_destination(destination, overwrite)
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(source), str(destination))
            self._record("file_move", action_target, "success")
            return str(destination)
        except Exception as exc:
            self._record("file_move", action_target, "error", str(exc))
            raise self._user_error(exc) from exc

    def delete_file(self, root: str, relative_path: str) -> str:
        action_target = f"{root}:{relative_path}"
        try:
            source = self._require_file(root, relative_path)
            timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
            trash_name = f"{timestamp}_{source.name}"
            destination = self._trash_dir / trash_name
            shutil.move(str(source), str(destination))
            self._record("file_delete", action_target, "success")
            return str(destination)
        except Exception as exc:
            self._record("file_delete", action_target, "error", str(exc))
            raise self._user_error(exc) from exc

    def destination_exists(self, root: str, relative_path: str) -> bool:
        try:
            destination = self._resolve_destination(root, relative_path)
            return destination.exists()
        except Exception as exc:
            raise self._user_error(exc) from exc

    def _require_root(self, root: str) -> Path:
        return PathGuard.ensure_allowed_root(root, self.list_allowed_roots())

    def _resolve_destination(self, root: str, relative_path: str) -> Path:
        root_path = self._require_root(root)
        destination = PathGuard.resolve_relative_path(root_path, relative_path)
        if destination == root_path:
            raise FileOperationError("Choose a file path inside the approved root folder.")
        return destination

    def _require_file(self, root: str, relative_path: str) -> Path:
        file_path = self._resolve_destination(root, relative_path)
        if not file_path.exists():
            raise FileNotFoundError(f"File does not exist: {file_path}")
        if not file_path.is_file():
            raise FileOperationError(f"Path is not a file: {file_path}")
        self._ensure_supported_extension(file_path)
        return file_path

    def _ensure_supported_extension(self, file_path: Path) -> None:
        if file_path.suffix.lower() not in SUPPORTED_TEXT_EXTENSIONS:
            raise UnsupportedFileTypeError(
                f"Unsupported file type: {file_path.suffix or '(no extension)'}"
            )


    def _ensure_creatable_extension(self, file_path: Path) -> None:
        if file_path.suffix.lower() not in CREATABLE_TEXT_EXTENSIONS:
            raise FileOperationError(
                "Creating new files is currently supported for .txt, .md, .csv, and .json only."
            )

    def _handle_existing_destination(self, destination: Path, overwrite: bool) -> None:
        if not destination.exists():
            return
        if not destination.is_file():
            raise FileOperationError(f"Destination is not a file: {destination}")
        if not overwrite:
            raise FileExistsError(f"Destination already exists: {destination}")
        destination.unlink()

    def _read_supported_file(self, file_path: Path) -> str:
        suffix = file_path.suffix.lower()
        if suffix in {".txt", ".md"}:
            return file_path.read_text(encoding="utf-8")
        if suffix == ".csv":
            with file_path.open("r", encoding="utf-8", newline="") as handle:
                rows = list(csv.reader(handle))
            return "\n".join(", ".join(column for column in row) for row in rows)
        if suffix == ".json":
            with file_path.open("r", encoding="utf-8") as handle:
                payload = json.load(handle)
            return json.dumps(payload, indent=2, ensure_ascii=False)
        if suffix == ".docx":
            try:
                from docx import Document
            except ModuleNotFoundError as exc:
                raise FileOperationError("DOCX reading requires python-docx to be installed.") from exc
            document = Document(file_path)
            return "\n".join(paragraph.text for paragraph in document.paragraphs)
        if suffix == ".doc":
            return self._read_doc_file(file_path)
        if suffix == ".pdf":
            try:
                from pypdf import PdfReader
            except ModuleNotFoundError as exc:
                raise FileOperationError("PDF reading requires pypdf to be installed.") from exc
            reader = PdfReader(str(file_path))
            return "\n\n".join(page.extract_text() or "" for page in reader.pages).strip()
        raise UnsupportedFileTypeError(f"Unsupported file type: {suffix}")

    def _read_doc_file(self, file_path: Path) -> str:
        attempts: list[str] = []
        available_methods: list[str] = []

        if os.name == "nt":
            available_methods.append("Microsoft Word")
            try:
                return self._read_doc_with_word_com(file_path)
            except Exception as exc:  # pragma: no cover - platform/dependency dependent
                attempts.append(f"Microsoft Word automation failed: {exc}")

        available_methods.append("LibreOffice")
        try:
            return self._read_doc_with_libreoffice(file_path)
        except Exception as exc:
            attempts.append(f"LibreOffice conversion failed: {exc}")

        available_methods.append("antiword")
        try:
            return self._read_doc_with_antiword(file_path)
        except Exception as exc:
            attempts.append(f"antiword extraction failed: {exc}")

        methods = ", ".join(available_methods)
        attempt_summary = " | ".join(attempts)
        raise FileOperationError(
            "Could not read this legacy .doc file. Install at least one supported extractor "
            f"({methods}) and try again. Details: {attempt_summary}"
        )

    def _read_doc_with_word_com(self, file_path: Path) -> str:
        try:
            import win32com.client  # type: ignore[import-untyped]
        except ModuleNotFoundError as exc:
            raise FileOperationError("pywin32 is not installed for Word automation.") from exc

        word = None
        document = None
        try:
            word = win32com.client.DispatchEx("Word.Application")
            word.Visible = False
            document = word.Documents.Open(str(file_path), ReadOnly=True)
            text = document.Content.Text or ""
            text = text.strip()
            if not text:
                raise FileOperationError("Word opened the file, but no readable text was extracted.")
            return text
        finally:  # pragma: no cover - platform/dependency dependent
            if document is not None:
                document.Close(False)
            if word is not None:
                word.Quit()

    def _read_doc_with_libreoffice(self, file_path: Path) -> str:
        executable = shutil.which("soffice") or shutil.which("libreoffice")
        if not executable:
            raise FileOperationError("LibreOffice command (soffice/libreoffice) was not found.")
        with tempfile.TemporaryDirectory() as temp_dir:
            subprocess.run(
                [
                    executable,
                    "--headless",
                    "--convert-to",
                    "txt:Text",
                    "--outdir",
                    temp_dir,
                    str(file_path),
                ],
                check=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
            output_path = Path(temp_dir) / f"{file_path.stem}.txt"
            if not output_path.exists():
                raise FileOperationError("LibreOffice did not produce a text output file.")
            text = output_path.read_text(encoding="utf-8", errors="replace").strip()
            if not text:
                raise FileOperationError("LibreOffice conversion completed, but extracted text was empty.")
            return text

    def _read_doc_with_antiword(self, file_path: Path) -> str:
        executable = shutil.which("antiword")
        if not executable:
            raise FileOperationError("antiword command was not found.")
        result = subprocess.run(
            [executable, str(file_path)],
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        text = (result.stdout or "").strip()
        if not text:
            raise FileOperationError("antiword ran, but extracted text was empty.")
        return text

    def _to_relative(self, root_path: Path, candidate: Path) -> str:
        if candidate == root_path:
            return "."
        return candidate.relative_to(root_path).as_posix()

    def _record(self, action_type: str, target: str, status: str, error_message: str = "") -> None:
        self._action_logger.record(action_type, target, status, error_message)

    def _user_error(self, exc: Exception) -> FileOperationError:
        if isinstance(exc, FileOperationError):
            return exc
        if isinstance(exc, PathAccessError):
            return FileOperationError(str(exc))
        if isinstance(exc, PermissionError):
            return FileOperationError(
                "Permission denied while accessing that file or folder."
            )
        if isinstance(exc, UnicodeDecodeError):
            return FileOperationError(
                "This file could not be read as UTF-8 text."
            )
        return FileOperationError(str(exc))
