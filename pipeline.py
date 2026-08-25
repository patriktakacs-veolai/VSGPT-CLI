"""Orchestrate document extraction and RAG staging-output generation."""

from __future__ import annotations

import argparse
import importlib
import json
import os
import sqlite3
import sys
import tempfile
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from pypdf import PdfReader, PdfWriter
from pypdf.errors import PdfReadError
from pydantic import BaseModel, ValidationError
import yaml

from connect_to_vsgpt import (
    API_BASE_URL,
    encode_pdf,
    get_access_token,
    load_env_file,
    request_json,
)
from document_scanner import DocumentScanner


PDF_CHUNK_SIZE = 10
DIGITAL_TEXT_THRESHOLD = 750
FILE_PROCESSING_DELAY_SECONDS = 5
MAX_EXTRACTION_ATTEMPTS = 3
RETRY_DELAY_SECONDS = 5
EXTENSION_GROUPS: dict[str, set[str]] = {
    "pdf": {".pdf"},
    "word": {".docx", ".doc"},
    "excel": {".xlsx", ".xls"},
    "image": {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".tiff"},
}
CHUNK_SUMMARY_PROMPT = (
    "Kérlek, készíts egy rendkívül részletes, minden adatra, szabályra és fontos témára "
    "kiterjedő összefoglalót ebből a dokumentumrészletből."
)


@dataclass(frozen=True)
class PipelineConfig:
    """Runtime settings and the dynamically loaded extraction schema."""

    schema_class: type[BaseModel]
    prompt: str
    target_dir: Path | None
    db_path: Path | None
    staging_dir: Path | None
    model: str


class VisionRegionLimitationError(RuntimeError):
    """Raised when the Vision endpoint rejects file input in the active region."""


def parse_args() -> argparse.Namespace:
    """Parse the pipeline's source, state, and output paths."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "target_dir",
        nargs="?",
        type=Path,
        help="Directory containing documents to process; overrides the config value.",
    )
    parser.add_argument(
        "db_path",
        nargs="?",
        type=Path,
        help="SQLite database used to track processed files; overrides the config value.",
    )
    parser.add_argument(
        "--config",
        type=Path,
        required=True,
        help="YAML file defining the extraction schema and prompt.",
    )
    parser.add_argument(
        "--staging-dir",
        type=Path,
        help="Directory for generated RAG text files; overrides the config value.",
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


def load_extraction_config(config_path: Path) -> PipelineConfig:
    """Load runtime settings, the extraction schema, and prompt from YAML."""
    try:
        with config_path.open(encoding="utf-8") as config_file:
            config = yaml.safe_load(config_file)
    except yaml.YAMLError as error:
        raise ValueError(f"Invalid YAML configuration: {config_path}") from error

    if not isinstance(config, dict):
        raise ValueError("Configuration must contain a YAML object.")

    schema_module = config.get("schema_module")
    schema_class_name = config.get("schema_class")
    prompt = config.get("prompt")
    model = config.get("model", "gpt-4o")
    if not isinstance(schema_module, str) or not schema_module:
        raise ValueError("Configuration field 'schema_module' must be a non-empty string.")
    if not isinstance(schema_class_name, str) or not schema_class_name:
        raise ValueError("Configuration field 'schema_class' must be a non-empty string.")
    if not isinstance(prompt, str) or not prompt:
        raise ValueError("Configuration field 'prompt' must be a non-empty string.")
    if not isinstance(model, str) or not model:
        raise ValueError("Configuration field 'model' must be a non-empty string.")

    try:
        module = importlib.import_module(schema_module)
    except ImportError as error:
        raise ValueError(f"Unable to import schema module '{schema_module}'.") from error

    schema_class = getattr(module, schema_class_name, None)
    if not isinstance(schema_class, type) or not issubclass(schema_class, BaseModel):
        raise ValueError(
            f"Schema class '{schema_class_name}' in module '{schema_module}' must inherit BaseModel."
        )

    path_values: dict[str, Path | None] = {}
    for field_name in ("target_dir", "db_path", "staging_dir"):
        value = config.get(field_name)
        if value is not None and (not isinstance(value, str) or not value):
            raise ValueError(f"Configuration field '{field_name}' must be a non-empty string.")
        path_values[field_name] = Path(value) if value is not None else None

    return PipelineConfig(
        schema_class=schema_class,
        prompt=prompt,
        target_dir=path_values["target_dir"],
        db_path=path_values["db_path"],
        staging_dir=path_values["staging_dir"],
        model=model,
    )


def build_response_format(schema_class: type[BaseModel]) -> dict[str, Any]:
    """Build the Veolia-compatible object schema for a Pydantic model."""
    schema = schema_class.model_json_schema()

    # Eltávolítjuk a title-t, mert az OpenAI schema formátuma néha kényes rá
    if "title" in schema:
        del schema["title"]

    schema["additionalProperties"] = False

    return {
        "type": "json_schema",
        "json_schema": {
            "name": schema_class.__name__.lower(), # Csak kisbetűs, érvényes név
            "schema": schema,
            "strict": True # Ezzel kényszerítjük ki, hogy ne térjen el!
        }
    }


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


def build_rag_document(extraction: BaseModel, source_path: Path) -> str:
    """Create RAG text with YAML metadata from a validated extraction response."""
    extraction_data = extraction.model_dump()
    title = extraction_data.get("title")
    category = extraction_data.get("category")
    tags = extraction_data.get("tags")
    summary = extraction_data.get("summary")
    questions_answered = extraction_data.get("questions_answered")
    if not all(
        (
            isinstance(title, str),
            isinstance(category, str),
            isinstance(tags, list) and all(isinstance(tag, str) for tag in tags),
            isinstance(summary, str),
            isinstance(questions_answered, list)
            and all(isinstance(question, str) for question in questions_answered),
        )
    ):
        raise ValueError("The configured extraction schema lacks the fields required for RAG output.")

    last_modified = datetime.fromtimestamp(
        source_path.stat().st_mtime, tz=timezone.utc
    ).date().isoformat()

    front_matter = [
        "---",
        f"doc_id: {_yaml_string(str(uuid.uuid4()))}",
        f"title: {_yaml_string(title)}",
        f"category: {_yaml_string(category)}",
        f"tags: {json.dumps(tags, ensure_ascii=False)}",
        f"source_path: {_yaml_string(str(source_path))}",
        f"last_modified: {_yaml_string(last_modified)}",
        "---",
    ]
    question_lines = [f"- {question}" for question in questions_answered]

    return "\n".join(
        [
            *front_matter,
            "",
            f"# {title}",
            "",
            "## Összefoglaló",
            summary,
            "",
            "## Megválaszolt kérdések",
            *question_lines,
            "",
        ]
    )


def _validate_extraction(answer: str, schema_class: type[BaseModel]) -> BaseModel:
    """Validate a structured API response with the extraction model."""
    print("\n--- NYERS JSON VÁLASZ ---")
    print(answer)
    print("-------------------------\n")
    return schema_class.model_validate_json(answer)


def _request_pdf_answer(
    file_path: Path,
    prompt: str,
    headers: dict[str, str],
    *,
    model: str,
) -> str:
    """Send one PDF to the extraction endpoint and return its text answer."""
    payload: dict[str, Any] = {
        "model": model,
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

    try:
        response = request_json(
            f"{API_BASE_URL}/answer",
            method="POST",
            headers=headers,
            payload=payload,
        )
    except RuntimeError as error:
        if (
            str(error).startswith("HTTP 400")
            and "File input is not supported in this region" in str(error)
        ):
            raise VisionRegionLimitationError("API Region limitation for Vision model") from error
        raise
    answer = response.get("answer")
    if not isinstance(answer, str):
        raise RuntimeError("Extraction response did not contain a JSON string in 'answer'.")
    return answer


def _summarize_pdf_chunks(reader: PdfReader, headers: dict[str, str], model: str) -> list[str]:
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
            chunk_summaries.append(
                _request_pdf_answer(temporary_path, CHUNK_SUMMARY_PROMPT, headers, model=model)
            )
        finally:
            if temporary_path is not None:
                temporary_path.unlink(missing_ok=True)

    return chunk_summaries


def _reduce_chunk_summaries(
    chunk_summaries: list[str],
    prompt: str,
    headers: dict[str, str],
    schema_class: type[BaseModel],
    model: str,
) -> BaseModel:
    """Create the final extraction JSON from the summaries of all PDF chunks."""
    joined_summaries = "\n\n".join(chunk_summaries)
    response = request_json(
        f"{API_BASE_URL}/chat/completions",
        method="POST",
        headers=headers,
        payload={
            "model": model,
            "messages": [
                {
                    "role": "user",
                    "content": f"{prompt}\n\nItt van a dokumentum tartalma:\n{joined_summaries}",
                }
            ],
            "temperature": 0.2,
            "user": os.environ["VSGPT_USER_EMAIL"],
            "response_format": build_response_format(schema_class),
        },
    )
    try:
        answer = response["choices"][0]["message"]["content"]
    except (IndexError, KeyError, TypeError) as error:
        raise RuntimeError("Reduce response did not contain choices[0].message.content.") from error

    if not isinstance(answer, str):
        raise RuntimeError("Reduce response content is not a JSON string.")
    return _validate_extraction(answer, schema_class)


def _extract_text_pdf(
    text: str,
    prompt: str,
    headers: dict[str, str],
    schema_class: type[BaseModel],
    model: str,
) -> BaseModel:
    """Extract metadata from locally available PDF text via the text endpoint."""
    response = request_json(
        f"{API_BASE_URL}/chat/completions",
        method="POST",
        headers=headers,
        payload={
            "model": model,
            "messages": [
                {
                    "role": "user",
                    "content": f"{prompt}\n\nItt van a dokumentum tartalma:\n{text}",
                }
            ],
            "temperature": 0.2,
            "user": os.environ["VSGPT_USER_EMAIL"],
            "response_format": build_response_format(schema_class),
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
    return _validate_extraction(answer, schema_class)


def extract_document(
    file_path: Path,
    prompt: str,
    headers: dict[str, str],
    schema_class: type[BaseModel],
    model: str,
) -> BaseModel:
    """Route digital PDFs to text extraction and scanned PDFs to the Vision flow."""
    try:
        reader = PdfReader(file_path)
    except PdfReadError as error:
        raise ValueError(f"Unable to read PDF {file_path}: {error}") from error

    extracted_text = "\n".join(page.extract_text() or "" for page in reader.pages).strip()
    if len(extracted_text) > DIGITAL_TEXT_THRESHOLD:
        print(f"Útvonal: Digitális PDF -> /chat/completions ({file_path})")
        return _extract_text_pdf(extracted_text, prompt, headers, schema_class, model)

    if len(reader.pages) <= PDF_CHUNK_SIZE:
        print(f"Útvonal: Szkennelt PDF -> /answer ({file_path})")
        return _validate_extraction(
            _request_pdf_answer(
                file_path,
                prompt,
                headers,
                model=model,
            ),
            schema_class,
        )

    print(f"Útvonal: Hosszú szkennelt PDF -> /answer + /chat/completions ({file_path})")
    chunk_summaries = _summarize_pdf_chunks(reader, headers, model)
    return _reduce_chunk_summaries(chunk_summaries, prompt, headers, schema_class, model)


def _is_retryable_extraction_error(error: RuntimeError | ValidationError) -> bool:
    """Return whether an extraction error is caused by throttling or an empty response."""
    if isinstance(error, RuntimeError):
        return str(error).startswith("HTTP 503")

    return any(
        detail.get("type") == "json_invalid" and detail.get("input") == ""
        for detail in error.errors()
    )


def extract_document_with_retry(
    file_path: Path,
    prompt: str,
    headers: dict[str, str],
    schema_class: type[BaseModel],
    model: str,
) -> BaseModel:
    """Extract a document with bounded retries for throttling and empty responses."""
    for attempt in range(1, MAX_EXTRACTION_ATTEMPTS + 1):
        try:
            return extract_document(file_path, prompt, headers, schema_class, model)
        except (RuntimeError, ValidationError) as error:
            if not _is_retryable_extraction_error(error) or attempt == MAX_EXTRACTION_ATTEMPTS:
                raise
            print(
                f"Retrying {file_path} after a transient extraction error "
                f"({attempt}/{MAX_EXTRACTION_ATTEMPTS}).",
                file=sys.stderr,
            )
            time.sleep(RETRY_DELAY_SECONDS)

    raise RuntimeError("Extraction retry loop completed without a result.")


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
        config = load_extraction_config(args.config)
        target_dir = args.target_dir or config.target_dir
        db_path = args.db_path or config.db_path
        staging_dir = args.staging_dir or config.staging_dir
        if target_dir is None or db_path is None or staging_dir is None:
            raise ValueError(
                "target_dir, db_path, and staging_dir must be set in the configuration or command line."
            )

        scanner = DocumentScanner(target_dir, db_path)
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
        staging_dir.mkdir(parents=True, exist_ok=True)
    except OSError as error:
        print(f"Unable to create staging directory: {error}", file=sys.stderr)
        return 1

    has_started_extraction = False
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

        if has_started_extraction:
            time.sleep(FILE_PROCESSING_DELAY_SECONDS)
        has_started_extraction = True

        try:
            extraction = extract_document_with_retry(
                file_path,
                config.prompt,
                headers,
                config.schema_class,
                config.model,
            )
            rag_document = build_rag_document(extraction, file_path)
            drive, tail = os.path.splitdrive(str(file_path.resolve()))
            drive_clean = drive.replace(":", "").strip("\\/").replace("\\", "/")
            tail_clean = tail.strip("\\/")
            clean_path = Path(drive_clean) / Path(tail_clean)
            output_path = staging_dir / clean_path.with_suffix(".txt")
            output_path.parent.mkdir(parents=True, exist_ok=True)
            output_path.write_text(rag_document, encoding="utf-8")
            scanner.mark_as_processed(file_path)
        except VisionRegionLimitationError:
            print(f"Skipping: API Region limitation for Vision model - {file_path}", file=sys.stderr)
        except (OSError, RuntimeError, ValueError, sqlite3.Error) as error:
            print(f"Skipping {file_path}: {error}", file=sys.stderr)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
