import io
import os
import uuid
import json
import time
import base64
import shutil
import subprocess
from pathlib import Path
from typing import List, Optional

import fitz
import boto3
from botocore.client import Config
from fastapi import FastAPI, HTTPException, Request
from fastapi import UploadFile, File
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from pypdf import PdfReader, PdfWriter

BASE_DIR = Path(__file__).resolve().parent
STATIC_DIR = BASE_DIR / "static"
UPLOAD_DIR = Path(os.getenv("UPLOAD_DIR", BASE_DIR / "uploads"))
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)

APP_TITLE = os.getenv("APP_TITLE", "PDF Mini App")
MAX_FILE_SIZE_MB = int(os.getenv("MAX_FILE_SIZE_MB", "80"))
FILE_TTL_SECONDS = int(os.getenv("FILE_TTL_SECONDS", str(24 * 3600)))
CLEANUP_ON_REQUEST = os.getenv("CLEANUP_ON_REQUEST", "true").lower() == "true"

USE_S3 = os.getenv("USE_S3", "false").lower() == "true"
S3_BUCKET = os.getenv("S3_BUCKET", "")
S3_ENDPOINT_URL = os.getenv("S3_ENDPOINT_URL", "")
S3_REGION = os.getenv("S3_REGION", "auto")
AWS_ACCESS_KEY_ID = os.getenv("AWS_ACCESS_KEY_ID", "")
AWS_SECRET_ACCESS_KEY = os.getenv("AWS_SECRET_ACCESS_KEY", "")

app = FastAPI(title=APP_TITLE)
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")
UPLOADED_OBJECTS: dict[str, dict] = {}

def get_s3_client():
    return boto3.client(
        "s3",
        endpoint_url=S3_ENDPOINT_URL or None,
        region_name=S3_REGION,
        aws_access_key_id=AWS_ACCESS_KEY_ID or None,
        aws_secret_access_key=AWS_SECRET_ACCESS_KEY or None,
        config=Config(signature_version="s3v4"),
    )

def generate_presigned_put_url(object_key: str, content_type: str = "application/pdf", expires_in: int = 900) -> str:
    s3 = get_s3_client()
    return s3.generate_presigned_url(
        ClientMethod="put_object",
        Params={"Bucket": S3_BUCKET, "Key": object_key, "ContentType": content_type},
        ExpiresIn=expires_in,
        HttpMethod="PUT",
    )

def read_object_bytes(object_key: str) -> bytes:
    if USE_S3:
        s3 = get_s3_client()
        obj = s3.get_object(Bucket=S3_BUCKET, Key=object_key)
        return obj["Body"].read()
    return (UPLOAD_DIR / object_key).read_bytes()

def object_exists(object_key: str) -> bool:
    if USE_S3:
        s3 = get_s3_client()
        try:
            s3.head_object(Bucket=S3_BUCKET, Key=object_key)
            return True
        except Exception:
            return False
    return (UPLOAD_DIR / object_key).exists()

def store_local_bytes(path_name: str, data: bytes) -> None:
    path = UPLOAD_DIR / path_name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)

def maybe_cleanup_uploads() -> None:
    if not CLEANUP_ON_REQUEST or USE_S3:
        return
    now = time.time()
    for path in UPLOAD_DIR.rglob("*"):
        if path.is_file():
            try:
                if now - path.stat().st_mtime > FILE_TTL_SECONDS:
                    path.unlink(missing_ok=True)
            except Exception:
                pass

class PageItem(BaseModel):
    token: Optional[str] = None
    source_index: Optional[int] = None
    rotation: int = 0
    is_blank: bool = False
    width: float = 595
    height: float = 842
    label: Optional[str] = None
    source_name: Optional[str] = None

class ExportPayload(BaseModel):
    items: List[PageItem]
    compression: str = "none"

class UploadInitPayload(BaseModel):
    filename: str
    content_type: str = "application/pdf"
    size: int

class UploadCompletePayload(BaseModel):
    filename: str
    object_key: str
    password: str = ""

class LoadEditorPayload(BaseModel):
    tokens: list[str]
    passwords_json: str = "{}"

@app.middleware("http")
async def cleanup_middleware(request: Request, call_next):
    maybe_cleanup_uploads()
    response = await call_next(request)
    response.headers["Cache-Control"] = "no-store"
    response.headers["X-Content-Type-Options"] = "nosniff"
    return response

def decrypt_reader_from_bytes(pdf_bytes: bytes, password: str = "") -> PdfReader:
    try:
        reader = PdfReader(io.BytesIO(pdf_bytes))
    except Exception as e:
        msg = str(e).lower()
        if "cryptography" in msg or "aes" in msg:
            raise HTTPException(status_code=500, detail="Dipendenze AES/cryptography mancanti per questo PDF.")
        raise HTTPException(status_code=400, detail=f"Impossibile leggere il PDF: {e}")
    if reader.is_encrypted:
        if not password:
            raise HTTPException(status_code=400, detail="PDF protetto da password.")
        result = reader.decrypt(password)
        if result == 0:
            raise HTTPException(status_code=400, detail="Password PDF non corretta.")
    return reader

def compress_pdf_file(input_path: Path, level: str) -> Path:
    if level == "none":
        return input_path
    gs = shutil.which("gs")
    if gs is None:
        return input_path
    quality_map = {"low": "/printer", "medium": "/ebook", "high": "/screen"}
    setting = quality_map.get(level)
    if not setting:
        return input_path
    out_path = input_path.with_name(f"{input_path.stem}_{level}.pdf")
    cmd = [gs, "-sDEVICE=pdfwrite", "-dCompatibilityLevel=1.4", f"-dPDFSETTINGS={setting}", "-dNOPAUSE", "-dQUIET", "-dBATCH", f"-sOutputFile={out_path}", str(input_path)]
    result = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    if result.returncode == 0 and out_path.exists():
        return out_path
    return input_path

@app.get("/")
def index():
    return FileResponse(str(STATIC_DIR / "index.html"))

@app.get("/health")
def health():
    return JSONResponse({"status": "ok", "storage": "s3" if USE_S3 else "local"})

@app.post("/api/upload/init")
def upload_init(payload: UploadInitPayload):
    max_file_size_bytes = MAX_FILE_SIZE_MB * 1024 * 1024
    if not payload.filename.lower().endswith(".pdf"):
        raise HTTPException(status_code=400, detail="Sono accettati solo file PDF.")
    if payload.size > max_file_size_bytes:
        raise HTTPException(status_code=413, detail=f"{payload.filename} supera il limite di {MAX_FILE_SIZE_MB} MB.")
    object_key = f"tmp/{uuid.uuid4()}_{payload.filename}"
    if USE_S3:
        upload_url = generate_presigned_put_url(object_key=object_key, content_type=payload.content_type or "application/pdf", expires_in=900)
        return {"object_key": object_key, "upload_url": upload_url, "direct": True}
    return {"object_key": object_key, "upload_url": "/api/upload/local", "direct": False}

@app.post("/api/upload/local")
async def upload_local(file: UploadFile = File(...)):
    if not file.filename.lower().endswith(".pdf"):
        raise HTTPException(status_code=400, detail="Sono accettati solo file PDF.")
    data = await file.read()
    if len(data) > MAX_FILE_SIZE_MB * 1024 * 1024:
        raise HTTPException(status_code=413, detail="File troppo grande.")
    object_key = f"tmp/{uuid.uuid4()}_{file.filename}"
    store_local_bytes(object_key, data)
    return {"object_key": object_key, "filename": file.filename}

@app.post("/api/upload/complete")
def upload_complete(payload: UploadCompletePayload):
    if not object_exists(payload.object_key):
        raise HTTPException(status_code=404, detail="Oggetto non trovato su storage.")
    token = str(uuid.uuid4())
    UPLOADED_OBJECTS[token] = {"filename": payload.filename, "object_key": payload.object_key, "uploaded_at": time.time(), "password": payload.password or ""}
    return {"token": token, "filename": payload.filename, "object_key": payload.object_key}

@app.post("/api/editor/load")
def load_editor_from_uploaded(payload: LoadEditorPayload):
    try:
        passwords = json.loads(payload.passwords_json or "{}")
    except Exception:
        passwords = {}
    all_pages = []
    sources = []
    for token in payload.tokens:
        if token not in UPLOADED_OBJECTS:
            raise HTTPException(status_code=404, detail=f"Upload token non trovato: {token}")
        meta = UPLOADED_OBJECTS[token]
        filename = meta["filename"]
        pdf_bytes = read_object_bytes(meta["object_key"])
        password = passwords.get(filename, meta.get("password", ""))
        reader = decrypt_reader_from_bytes(pdf_bytes, password)
        doc = fitz.open(stream=pdf_bytes, filetype="pdf")
        if doc.needs_pass and not doc.authenticate(password):
            raise HTTPException(status_code=400, detail=f"Password non corretta per {filename}.")
        page_count = len(reader.pages)
        sources.append({"token": token, "filename": filename, "page_count": page_count})
        for i in range(page_count):
            page = doc.load_page(i)
            pix = page.get_pixmap(matrix=fitz.Matrix(0.22, 0.22), alpha=False)
            b64 = base64.b64encode(pix.tobytes("png")).decode("utf-8")
            rect = page.rect
            all_pages.append({"label": f"{filename} · Pag. {i+1}", "source_name": filename, "token": token, "source_index": i, "rotation": 0, "is_blank": False, "width": rect.width, "height": rect.height, "thumb": f"data:image/png;base64,{b64}"})
    return {"sources": sources, "pages": all_pages, "total_pages": len(all_pages)}

@app.post("/api/export")
def export_pdf(payload: ExportPayload):
    readers = {}
    writer = PdfWriter()
    for item in payload.items:
        if item.is_blank:
            writer.add_blank_page(width=item.width, height=item.height)
            continue
        if not item.token or item.source_index is None:
            continue
        if item.token not in readers:
            if item.token not in UPLOADED_OBJECTS:
                raise HTTPException(status_code=404, detail=f"Sorgente non trovata: {item.token}")
            meta = UPLOADED_OBJECTS[item.token]
            data = read_object_bytes(meta["object_key"])
            password = meta.get("password", "")
            readers[item.token] = decrypt_reader_from_bytes(data, password)
        page = readers[item.token].pages[item.source_index]
        if item.rotation:
            page = page.rotate(item.rotation)
        writer.add_page(page)
    out_path = UPLOAD_DIR / f"export_{uuid.uuid4()}.pdf"
    with open(out_path, "wb") as f:
        writer.write(f)
    final_path = compress_pdf_file(out_path, payload.compression)
    return {"download_url": f"/api/download/{final_path.name}"}

@app.get("/api/download/{filename}")
def download_file(filename: str):
    path = UPLOAD_DIR / filename
    if not path.exists():
        raise HTTPException(status_code=404, detail="File non trovato.")
    return FileResponse(str(path), media_type="application/pdf", filename=filename)
