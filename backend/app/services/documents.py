"""Plain-text extraction for uploaded resumes / job descriptions (PDF, TXT, MD)."""
import io
import re

MAX_UPLOAD_BYTES = 2 * 1024 * 1024
MAX_PDF_PAGES = 10
MAX_TEXT_CHARS = 30_000


class ExtractionError(ValueError):
    pass


def _clean(text: str) -> str:
    text = text.replace("\x00", "")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()[:MAX_TEXT_CHARS]


def extract_text(filename: str, data: bytes) -> str:
    if len(data) > MAX_UPLOAD_BYTES:
        raise ExtractionError("File is larger than 2 MB")
    name = filename.lower()
    if name.endswith(".pdf") or data[:5] == b"%PDF-":
        from pypdf import PdfReader
        from pypdf.errors import PdfReadError

        try:
            reader = PdfReader(io.BytesIO(data))
            if reader.is_encrypted:
                raise ExtractionError("Password-protected PDFs are not supported")
            text = "\n".join((page.extract_text() or "") for page in reader.pages[:MAX_PDF_PAGES])
        except (PdfReadError, ValueError, KeyError) as e:
            raise ExtractionError("Could not read this PDF") from e
    elif name.endswith((".txt", ".md")):
        text = data.decode("utf-8", errors="replace")
    else:
        raise ExtractionError("Upload a PDF, .txt or .md file (or paste the text instead)")
    text = _clean(text)
    if len(text) < 30:
        raise ExtractionError("No readable text found (scanned PDFs aren't supported - paste the text instead)")
    return text
