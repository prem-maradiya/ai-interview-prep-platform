from typing import Annotated, Literal

import anyio
from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile, status
from sqlalchemy import func, select

from app.api.deps import DB, CurrentUser, get_scoped
from app.db.models import Document, User
from app.schemas import DocumentIn, DocumentOut, DocumentSummary, FitIn
from app.services import quota
from app.services.documents import MAX_UPLOAD_BYTES, ExtractionError, extract_text
from app.services.llm import FitReport, LLMError, LLMProvider, get_llm

router = APIRouter(prefix="/documents", tags=["documents"])
LLMDep = Annotated[LLMProvider, Depends(get_llm)]

MAX_DOCUMENTS_PER_USER = 20


async def own_document(db, doc_id: str, user: User, kind: str | None = None) -> Document:
    """Documents are private to their owner, even within the tenant."""
    doc = await get_scoped(db, Document, doc_id, user.tenant_id)
    if doc.user_id != user.id or (kind and doc.kind != kind):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Document not found")
    return doc


def _summary(d: Document) -> DocumentSummary:
    return DocumentSummary(id=d.id, kind=d.kind, title=d.title, chars=len(d.text), preview=d.text[:160], created_at=d.created_at)


async def _save(db, user: User, kind: str, title: str, text: str) -> Document:
    count = await db.scalar(select(func.count(Document.id)).where(Document.user_id == user.id))
    if count >= MAX_DOCUMENTS_PER_USER:
        raise HTTPException(status.HTTP_409_CONFLICT, f"You can keep up to {MAX_DOCUMENTS_PER_USER} documents; delete one first")
    doc = Document(tenant_id=user.tenant_id, user_id=user.id, kind=kind, title=title, text=text)
    db.add(doc)
    await db.commit()
    return doc


@router.post("", response_model=DocumentOut, status_code=201)
async def create_document(body: DocumentIn, user: CurrentUser, db: DB):
    """Save a pasted resume or job description."""
    return await _save(db, user, body.kind, body.title, body.text.strip())


@router.post("/upload", response_model=DocumentOut, status_code=201)
async def upload_document(
    user: CurrentUser, db: DB,
    kind: Annotated[Literal["resume", "job_description"], Form()],
    file: Annotated[UploadFile, File()],
    title: Annotated[str | None, Form(max_length=200)] = None,
):
    """Upload a PDF / TXT / MD file; only the extracted text is stored, not the file."""
    data = await file.read(MAX_UPLOAD_BYTES + 1)
    try:
        text = await anyio.to_thread.run_sync(extract_text, file.filename or "", data)
    except ExtractionError as e:
        raise HTTPException(422, str(e))
    return await _save(db, user, kind, (title or file.filename or kind)[:200], text)


@router.get("", response_model=list[DocumentSummary])
async def list_documents(user: CurrentUser, db: DB, kind: str | None = None):
    q = select(Document).where(Document.tenant_id == user.tenant_id, Document.user_id == user.id)
    if kind:
        q = q.where(Document.kind == kind)
    return [_summary(d) for d in (await db.scalars(q.order_by(Document.created_at.desc()))).all()]


@router.get("/{doc_id}", response_model=DocumentOut)
async def get_document(doc_id: str, user: CurrentUser, db: DB):
    return await own_document(db, doc_id, user)


@router.delete("/{doc_id}", status_code=204)
async def delete_document(doc_id: str, user: CurrentUser, db: DB):
    await db.delete(await own_document(db, doc_id, user))
    await db.commit()


@router.post("/fit", response_model=FitReport)
async def fit_report(body: FitIn, user: CurrentUser, db: DB, llm: LLMDep):
    """Compare a resume with a job description: match score, gaps, resume edits, likely questions."""
    resume = await own_document(db, body.resume_id, user, "resume")
    jd = await own_document(db, body.job_description_id, user, "job_description")
    await quota.consume(db, user, "llm")
    try:
        report = await llm.fit_report(resume.text, jd.text)
    except LLMError as e:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(e))
    await db.commit()
    return report
