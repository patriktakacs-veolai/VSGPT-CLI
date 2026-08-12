"""Local file scanner for the first stage of a RAG ETL pipeline."""

from __future__ import annotations

import hashlib
import sqlite3
from pathlib import Path
from typing import Union


PathLike = Union[str, Path]
HASH_CHUNK_SIZE = 64 * 1024


class DocumentScanner:
    """Find files whose content hashes have not yet been processed."""

    def __init__(self, target_dir: PathLike, db_path: PathLike) -> None:
        """Initialize the scanner with a directory and SQLite state database."""
        self.target_dir = Path(target_dir).expanduser().resolve()
        self.db_path = Path(db_path).expanduser().resolve()

        if not self.target_dir.is_dir():
            raise ValueError(f"Target directory does not exist or is not a directory: {self.target_dir}")

        self._initialize_database()

    def _initialize_database(self) -> None:
        """Create the processed-files state table when it does not exist."""
        connection = sqlite3.connect(self.db_path)
        try:
            with connection:
                connection.execute(
                    """
                    CREATE TABLE IF NOT EXISTS processed_files (
                        id INTEGER PRIMARY KEY,
                        file_path TEXT,
                        file_hash TEXT,
                        processed_at TIMESTAMP
                    )
                    """
                )
        finally:
            connection.close()

    def _calculate_file_hash(self, file_path: Path) -> str:
        """Return the byte-level SHA-256 hash of a file."""
        digest = hashlib.sha256()

        with file_path.open("rb") as file_handle:
            for chunk in iter(lambda: file_handle.read(HASH_CHUNK_SIZE), b""):
                digest.update(chunk)

        return digest.hexdigest()

    def get_unprocessed_files(self) -> list[str]:
        """Return absolute paths of files whose hashes are absent from the database."""
        new_files: list[str] = []

        connection = sqlite3.connect(self.db_path)
        try:
            for file_path in self.target_dir.rglob("*"):
                if not file_path.is_file():
                    continue

                file_hash = self._calculate_file_hash(file_path)
                result = connection.execute(
                    "SELECT 1 FROM processed_files WHERE file_hash = ? LIMIT 1",
                    (file_hash,),
                ).fetchone()

                if result is None:
                    new_files.append(str(file_path.resolve()))
        finally:
            connection.close()

        return new_files

    def mark_as_processed(self, file_path: Path) -> None:
        """Store a file's hash and the current UTC processing timestamp."""
        resolved_path = file_path.resolve()
        file_hash = self._calculate_file_hash(resolved_path)
        connection = sqlite3.connect(self.db_path)
        try:
            with connection:
                connection.execute(
                    """
                    INSERT INTO processed_files (file_path, file_hash, processed_at)
                    VALUES (?, ?, CURRENT_TIMESTAMP)
                    """,
                    (str(resolved_path), file_hash),
                )
        finally:
            connection.close()


if __name__ == "__main__":
    scanner = DocumentScanner(
        target_dir=Path("test_documents"),
        db_path=Path("scanner_state.sqlite"),
    )

    for document_path in scanner.get_unprocessed_files():
        print(document_path)
