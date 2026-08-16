"""User-uploaded knowledge files for the chat's RAG index."""
from __future__ import annotations

import datetime as dt
import io
import re

from fastapi import APIRouter, HTTPException, UploadFile

from .. import rag

router = APIRouter(prefix="/api")

MAX_BYTES = 8 * 1024 * 1024  # 8 MB
TEXT_EXTENSIONS = (".txt", ".md", ".csv", ".json", ".log")


def _safe_name(filename: str) -> str:
    name = re.sub(r"[^A-Za-z0-9._\- ]", "_", filename or "file").strip()
    return name[:120] or "file"


def _extract_text(filename: str, data: bytes) -> str:
    if filename.lower().endswith(".pdf"):
        try:
            from pypdf import PdfReader

            reader = PdfReader(io.BytesIO(data))
            return "\n\n".join(page.extract_text() or "" for page in reader.pages)
        except Exception as e:
            raise HTTPException(400, f"Could not read PDF: {e}")
    if filename.lower().endswith(TEXT_EXTENSIONS):
        return data.decode("utf-8", errors="replace")
    raise HTTPException(
        400,
        "Unsupported file type. Upload .pdf, .txt, .md, .csv, .json, or .log.",
    )


@router.post("/documents", status_code=201)
async def upload_document(file: UploadFile):
    data = await file.read()
    if len(data) > MAX_BYTES:
        raise HTTPException(413, "File too large (max 8 MB).")
    name = _safe_name(file.filename)
    text = _extract_text(name, data).strip()
    if not text:
        raise HTTPException(400, "No readable text found in the file.")
    chunks = rag.index_file(name, text, dt.date.today().isoformat())
    return {"name": name, "chunks": chunks}


@router.get("/documents")
def list_documents():
    return {"files": rag.list_files()}


@router.delete("/documents/{name}")
def delete_document(name: str):
    removed = rag.delete_file(_safe_name(name))
    if removed == 0:
        raise HTTPException(404, "No such file in the knowledge index.")
    return {"removed": name, "chunks": removed}
