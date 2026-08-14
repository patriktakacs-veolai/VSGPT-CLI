"""Orchestrate document extraction and RAG staging-output generation."""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
import tempfile
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from pypdf import PdfReader, PdfWriter
from pypdf.errors import PdfReadError
from pydantic import BaseModel, Field

from connect_to_vsgpt import (
    API_BASE_URL,
    encode_pdf,
    get_access_token,
    load_env_file,
    request_json,
)
from document_scanner import DocumentScanner


PDF_CHUNK_SIZE = 45
DIGITAL_TEXT_THRESHOLD = 1000
EXTENSION_GROUPS: dict[str, set[str]] = {
    "pdf": {".pdf"},
    "word": {".docx", ".doc"},
    "excel": {".xlsx", ".xls"},
    "image": {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".tiff"},
}
BASE_EXTRACTION_PROMPT = (
    "Te egy professzionális dokumentum-elemző rendszer vagy. "
    "A feladatod a csatolt dokumentum elolvasása, és az abban található "
    "legfontosabb információk strukturált kinyerése a megadott séma alapján."
)
CHUNK_SUMMARY_PROMPT = (
    "Kérlek, készíts egy rendkívül részletes, minden adatra, szabályra és fontos témára "
    "kiterjedő összefoglalót ebből a dokumentumrészletből."
)


class DocumentExtraction(BaseModel):
    """Structured document metadata returned by the extraction model."""

    title: str = Field(description="A dokumentum eredeti címe.")
    category: str = Field(description="A dokumentum témaköre / kategóriája (pl. IT-Biztonság, HR, Jogi, Pénzügy).")
    tags: list[str] = Field(
        min_length=7,
        max_length=7,
        description="Pontosan 7 darab releváns kulcsszó vagy címke."
    )
    summary: str = Field(description="Egy alapos, részletes leírás a dokumentum lényegi tartalmáról.")
    questions_answered: list[str] = Field(
        min_length=3,
        max_length=5,
        description="3-5 darab legfontosabb kérdés, amire a szöveg megoldást kínál."
    )


DOCUMENT_EXTRACTION_RESPONSE_FORMAT: dict[str, Any] = {
    "type": "json_schema",
    "json_schema": {
        "name": "document_extraction",
        "schema": DocumentExtraction.model_json_schema(),
    },
}


def parse_args() -> argparse.Namespace:
    """Parse the pipeline's source, state, and output paths."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("target_dir", type=Path, help="Directory containing documents to process.")
    parser.add_argument("db_path", type=Path, help="SQLite database used to track processed files.")
    parser.add_argument(
        "--staging-dir",
        type=Path,
        default=Path("staging_output"),
        help="Directory for generated RAG text files (default: staging_output).",
    )
    parser.add_argument(
        "--file-type",
        choices=["all", *EXTENSION_GROUPS],
        default="pdf",
        help="File type to process (default: pdf).",
    )
    parser.add_argument(
        "--pdf-type",
        choices=["all", "digital", "scanned"],
        default="all",
        help="PDF type to process (default: all).",
    )
    return parser.parse_args()


def is_file_type_matching(file_path: Path, file_type_filter: str) -> bool:
    """Return whether a file belongs to the requested extension group."""
    if file_type_filter == "all":
        return True
    if file_type_filter not in EXTENSION_GROUPS:
        raise ValueError(f"Unsupported file type filter: {file_type_filter}")
    return file_path.suffix.lower() in EXTENSION_GROUPS[file_type_filter]


def is_pdf_type_matching(file_path: Path, pdf_type_filter: str) -> bool:
    """Return whether a PDF has locally extractable text matching the requested type."""
    if pdf_type_filter == "all":
        return True
    if pdf_type_filter not in {"digital", "scanned"}:
        raise ValueError(f"Unsupported PDF type filter: {pdf_type_filter}")

    reader = PdfReader(file_path)
    extracted_text = "\n".join(page.extract_text() or "" for page in reader.pages).strip()
    is_digital = len(extracted_text) > DIGITAL_TEXT_THRESHOLD
    return is_digital if pdf_type_filter == "digital" else not is_digital


def _yaml_string(value: str) -> str:
    """Return a JSON-quoted string, which is also valid YAML."""
    return json.dumps(value, ensure_ascii=False)


def build_rag_document(extraction: DocumentExtraction, source_path: Path) -> str:
    """Create RAG text with YAML metadata from a validated extraction response."""
    last_modified = datetime.fromtimestamp(
        source_path.stat().st_mtime, tz=timezone.utc
    ).date().isoformat()

    front_matter = [
        "---",
        f"doc_id: {_yaml_string(str(uuid.uuid4()))}",
        f"title: {_yaml_string(extraction.title)}",
        f"category: {_yaml_string(extraction.category)}",
        f"tags: {json.dumps(extraction.tags, ensure_ascii=False)}",
        f"source_path: {_yaml_string(str(source_path))}",
        f"last_modified: {_yaml_string(last_modified)}",
        "---",
    ]
    question_lines = [f"- {question}" for question in extraction.questions_answered]

    return "\n".join(
        [
            *front_matter,
            "",
            f"# {extraction.title}",
            "",
            "## Összefoglaló",
            extraction.summary,
            "",
            "## Megválaszolt kérdések",
            *question_lines,
            "",
        ]
    )


def _validate_extraction(answer: str) -> DocumentExtraction:
    """Validate a structured API response with the extraction model."""
    return DocumentExtraction.model_validate_json(answer)


def _request_pdf_answer(
    file_path: Path, prompt: str, headers: dict[str, str], *, structured_output: bool = False
) -> str:
    """Send one PDF to the extraction endpoint and return its text answer."""
    payload: dict[str, Any] = {
        "model": "gpt-4o",
        "history": [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": prompt},
                    {"type": "image_url", "image_url": {"url": encode_pdf(file_path)}},
                ],
            }
        ],
        "temperature": 0.2,
        "useremail": os.environ["VSGPT_USER_EMAIL"],
    }
    if structured_output:
        payload["response_format"] = DOCUMENT_EXTRACTION_RESPONSE_FORMAT

    response = request_json(
        f"{API_BASE_URL}/answer",
        method="POST",
        headers=headers,
        payload=payload,
    )
    answer = response.get("answer")
    if not isinstance(answer, str):
        raise RuntimeError("Extraction response did not contain a JSON string in 'answer'.")
    return answer


def _summarize_pdf_chunks(reader: PdfReader, headers: dict[str, str]) -> list[str]:
    """Split a PDF into temporary chunks and collect detailed summaries."""
    chunk_summaries: list[str] = []

    for start_page in range(0, len(reader.pages), PDF_CHUNK_SIZE):
        writer = PdfWriter()
        for page in reader.pages[start_page : start_page + PDF_CHUNK_SIZE]:
            writer.add_page(page)

        temporary_path: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as temporary_file:
                temporary_path = Path(temporary_file.name)
                writer.write(temporary_file)
            chunk_summaries.append(_request_pdf_answer(temporary_path, CHUNK_SUMMARY_PROMPT, headers))
        finally:
            if temporary_path is not None:
                temporary_path.unlink(missing_ok=True)

    return chunk_summaries


def _reduce_chunk_summaries(
    chunk_summaries: list[str], prompt: str, headers: dict[str, str]
) -> DocumentExtraction:
    """Create the final extraction JSON from the summaries of all PDF chunks."""
    joined_summaries = "\n\n".join(chunk_summaries)
    response = request_json(
        f"{API_BASE_URL}/chat/completions",
        method="POST",
        headers=headers,
        payload={
            "model": "gpt-4o",
            "messages": [
                {
                    "role": "user",
                    "content": f"{prompt}\n\nItt van a dokumentum tartalma:\n{joined_summaries}",
                }
            ],
            "temperature": 0.2,
            "user": os.environ["VSGPT_USER_EMAIL"],
            "response_format": DOCUMENT_EXTRACTION_RESPONSE_FORMAT,
        },
    )
    try:
        answer = response["choices"][0]["message"]["content"]
    except (IndexError, KeyError, TypeError) as error:
        raise RuntimeError("Reduce response did not contain choices[0].message.content.") from error

    if not isinstance(answer, str):
        raise RuntimeError("Reduce response content is not a JSON string.")
    return _validate_extraction(answer)


def _extract_text_pdf(text: str, prompt: str, headers: dict[str, str]) -> DocumentExtraction:
    """Extract metadata from locally available PDF text via the text endpoint."""
    response = request_json(
        f"{API_BASE_URL}/chat/completions",
        method="POST",
        headers=headers,
        payload={
            "model": "gpt-4o-mini",
            "messages": [
                {
                    "role": "user",
                    "content": f"{prompt}\n\nItt van a dokumentum tartalma:\n{text}",
                }
            ],
            "temperature": 0.2,
            "user": os.environ["VSGPT_USER_EMAIL"],
            "response_format": DOCUMENT_EXTRACTION_RESPONSE_FORMAT,
        },
    )
    try:
        answer = response["choices"][0]["message"]["content"]
    except (IndexError, KeyError, TypeError) as error:
        raise RuntimeError(
            "Text extraction response did not contain choices[0].message.content."
        ) from error

    if not isinstance(answer, str):
        raise RuntimeError("Text extraction response content is not a JSON string.")
    return _validate_extraction(answer)


def extract_document(file_path: Path, prompt: str, headers: dict[str, str]) -> DocumentExtraction:
    """Route digital PDFs to text extraction and scanned PDFs to the Vision flow."""
    try:
        reader = PdfReader(file_path)
    except PdfReadError as error:
        raise ValueError(f"Unable to read PDF {file_path}: {error}") from error

    extracted_text = "\n".join(page.extract_text() or "" for page in reader.pages).strip()
    if len(extracted_text) > DIGITAL_TEXT_THRESHOLD:
        return _extract_text_pdf(extracted_text, prompt, headers)

    if len(reader.pages) <= PDF_CHUNK_SIZE:
        return _validate_extraction(
            _request_pdf_answer(file_path, prompt, headers, structured_output=True)
        )

    chunk_summaries = _summarize_pdf_chunks(reader, headers)
    return _reduce_chunk_summaries(chunk_summaries, prompt, headers)


def main() -> int:
    """Process untracked documents into RAG staging output."""
    args = parse_args()
    load_env_file(Path(".env"))

    client_id = os.getenv("VSGPT_CLIENT_ID")
    client_secret = os.getenv("VSGPT_CLIENT_SECRET")
    user_email = os.getenv("VSGPT_USER_EMAIL")
    if not client_id or not client_secret or not user_email:
        print(
            "Missing VSGPT_CLIENT_ID, VSGPT_CLIENT_SECRET, or VSGPT_USER_EMAIL.",
            file=sys.stderr,
        )
        return 2

    try:
        prompt = BASE_EXTRACTION_PROMPT
        scanner = DocumentScanner(args.target_dir, args.db_path)
        unprocessed_files = scanner.get_unprocessed_files()
        access_token = get_access_token(client_id, client_secret)
    except (OSError, RuntimeError, ValueError, sqlite3.Error) as error:
        print(f"Pipeline initialization failed: {error}", file=sys.stderr)
        return 1

    headers = {
        "Accept": "application/json",
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json",
    }
    try:
        args.staging_dir.mkdir(parents=True, exist_ok=True)
    except OSError as error:
        print(f"Unable to create staging directory: {error}", file=sys.stderr)
        return 1

    for file_path_string in unprocessed_files:
        file_path = Path(file_path_string)
        if not is_file_type_matching(file_path, args.file_type):
            if file_path.suffix.lower() == ".pdf":
                print(f"Skipping PDF {file_path}: it does not match --file-type {args.file_type}.")
            continue

        if file_path.suffix.lower() == ".pdf":
            try:
                matches_pdf_type = is_pdf_type_matching(file_path, args.pdf_type)
            except (OSError, PdfReadError) as error:
                print(f"Skipping PDF {file_path}: unable to determine PDF type: {error}", file=sys.stderr)
                continue
            if not matches_pdf_type:
                print(f"Skipping PDF {file_path}: it does not match --pdf-type {args.pdf_type}.")
                continue

        try:
            extraction = extract_document(file_path, prompt, headers)
            rag_document = build_rag_document(extraction, file_path)
            drive, tail = os.path.splitdrive(str(file_path.resolve()))
            drive_clean = drive.replace(":", "").strip("\\/").replace("\\", "/")
            tail_clean = tail.strip("\\/")
            clean_path = Path(drive_clean) / Path(tail_clean)
            output_path = args.staging_dir / clean_path.with_suffix(".txt")
            output_path.parent.mkdir(parents=True, exist_ok=True)
            output_path.write_text(rag_document, encoding="utf-8")
            scanner.mark_as_processed(file_path)
        except (OSError, RuntimeError, ValueError, sqlite3.Error) as error:
            print(f"Skipping {file_path}: {error}", file=sys.stderr)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
