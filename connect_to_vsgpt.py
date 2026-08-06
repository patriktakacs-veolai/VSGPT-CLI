"""Command-line client for the VeoliaSecureGPT OpenAI-compatible API."""

from __future__ import annotations

import argparse
import base64
import json
import os
import sys
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen


API_BASE_URL = "https://api.veolia.com/llm/veoliasecuregpt/v1"
TOKEN_URL = "https://api.veolia.com/security/v2/oauth/token"


def load_env_file(path: Path) -> None:
    """Load simple KEY=VALUE entries without overwriting shell variables."""
    if not path.is_file():
        return

    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue

        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
            value = value[1:-1]
        os.environ.setdefault(key, value)


def request_json(
    url: str, *, method: str, headers: dict[str, str], payload: dict[str, Any] | None = None
) -> dict[str, Any]:
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    request = Request(url, data=data, headers=headers, method=method)

    try:
        with urlopen(request, timeout=60) as response:
            return json.loads(response.read().decode("utf-8"))
    except HTTPError as error:
        details = error.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"HTTP {error.code} from {url}: {details}") from error
    except URLError as error:
        raise RuntimeError(f"Unable to reach {url}: {error.reason}") from error


def get_access_token(client_id: str, client_secret: str) -> str:
    credentials = base64.b64encode(f"{client_id}:{client_secret}".encode("utf-8")).decode("ascii")
    request = Request(
        TOKEN_URL,
        data=urlencode({"grant_type": "client_credentials"}).encode("ascii"),
        headers={
            "Accept": "application/json",
            "Authorization": f"Basic {credentials}",
            "Content-Type": "application/x-www-form-urlencoded",
        },
        method="POST",
    )

    try:
        with urlopen(request, timeout=30) as response:
            token_response = json.loads(response.read().decode("utf-8"))
    except HTTPError as error:
        details = error.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"OAuth token request failed (HTTP {error.code}): {details}") from error
    except URLError as error:
        raise RuntimeError(f"Unable to obtain OAuth token: {error.reason}") from error

    access_token = token_response.get("access_token")
    if not isinstance(access_token, str) or not access_token:
        raise RuntimeError("OAuth response did not contain an access_token.")
    return access_token


def encode_pdf(pdf_path: Path) -> str:
    """Return a PDF as the data URL required by the multimodal API."""
    if pdf_path.suffix.lower() != ".pdf":
        raise RuntimeError(f"Attachment must be a .pdf file: {pdf_path}")

    try:
        content = pdf_path.read_bytes()
    except OSError as error:
        raise RuntimeError(f"Unable to read PDF file {pdf_path}: {error}") from error

    if not content.startswith(b"%PDF-"):
        raise RuntimeError(f"Attachment is not a valid PDF file: {pdf_path}")

    return "data:application/pdf;base64," + base64.b64encode(content).decode("ascii")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("prompt", nargs="?", help="Question to send to the model.")
    parser.add_argument("--model", default="gpt-4o-mini", help="Model ID (default: gpt-4o-mini).")
    parser.add_argument(
        "--user-email",
        default=os.getenv("VSGPT_USER_EMAIL"),
        help="End-user email required by the VeoliaSecureGPT proxy.",
    )
    parser.add_argument("--temperature", type=float, default=0.2, help="Sampling temperature (0-2).")
    parser.add_argument("--pdf", type=Path, help="PDF attachment to include with the prompt.")
    parser.add_argument(
        "--generate_md",
        action="store_true",
        help="Use extraction_prompt.md as the prompt for a PDF attachment.",
    )
    parser.add_argument("--list-models", action="store_true", help="List accessible model IDs.")
    return parser.parse_args()


def main() -> int:
    load_env_file(Path(".env"))
    args = parse_args()

    pdf_prompt = args.prompt
    if args.pdf and args.generate_md:
        extraction_prompt_path = Path("extraction_prompt.md")
        if not extraction_prompt_path.is_file():
            print("Missing extraction_prompt.md in the project root.", file=sys.stderr)
            return 2
        try:
            pdf_prompt = extraction_prompt_path.read_text(encoding="utf-8")
        except OSError as error:
            print(f"Unable to read extraction_prompt.md: {error}", file=sys.stderr)
            return 2

    client_id = os.getenv("VSGPT_CLIENT_ID")
    client_secret = os.getenv("VSGPT_CLIENT_SECRET")
    if not client_id or not client_secret:
        print(
            "Missing VSGPT_CLIENT_ID or VSGPT_CLIENT_SECRET. Set them in .env or the environment.",
            file=sys.stderr,
        )
        return 2
    if not args.list_models and not pdf_prompt:
        print("Provide a prompt or use --list-models.", file=sys.stderr)
        return 2
    if not args.list_models and not args.user_email:
        print("Provide --user-email (or set VSGPT_USER_EMAIL).", file=sys.stderr)
        return 2
    if not 0 <= args.temperature <= 2:
        print("--temperature must be between 0 and 2.", file=sys.stderr)
        return 2

    try:
        access_token = get_access_token(client_id, client_secret)
        headers = {
            "Accept": "application/json",
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "application/json",
        }

        headers["Authorization"] = "Bearer " + access_token

        if args.list_models:
            response = request_json(f"{API_BASE_URL}/models", method="GET", headers=headers)
            for model in response.get("data", []):
                print(model.get("id", model))
            return 0

        content: str | list[dict[str, Any]] = args.prompt
        if args.pdf:
            content = [
                {"type": "text", "text": pdf_prompt},
                {"type": "image_url", "image_url": {"url": encode_pdf(args.pdf)}},
            ]

        if args.pdf:
            response = request_json(
                f"{API_BASE_URL}/answer",
                method="POST",
                headers=headers,
                payload={
                    "model": args.model,
                    "history": [{"role": "user", "content": content}],
                    "temperature": args.temperature,
                    "useremail": args.user_email,
                },
            )
        else:
            response = request_json(
                f"{API_BASE_URL}/chat/completions",
                method="POST",
                headers=headers,
                payload={
                    "model": args.model,
                    "messages": [{"role": "user", "content": content}],
                    "temperature": args.temperature,
                    "user": args.user_email,
                },
            )
    except RuntimeError as error:
        print(f"Request failed: {error}", file=sys.stderr)
        return 1

    if args.pdf:
        answer = response.get("answer")
        print(answer if answer is not None else json.dumps(response, ensure_ascii=False, indent=2))
        return 0

    choices = response.get("choices", [])
    if choices:
        content = choices[0].get("message", {}).get("content")
        print(content if content is not None else json.dumps(response, ensure_ascii=False, indent=2))
    else:
        print(json.dumps(response, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())