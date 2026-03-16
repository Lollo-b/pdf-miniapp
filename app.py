import io
import os
import uuid
import time
import json
import base64
import shutil
import subprocess
import logging
from pathlib import Path
from typing import List, Optional, Tuple

import fitz
import boto3
from botocore.client import Config
from fastapi import FastAPI, UploadFile, File, Form, HTTPException, Request, Depends
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from pydantic import BaseModel
from pypdf import PdfReader, PdfWriter
import secrets

BASE_DIR = Path(__file__).resolve().parent
UPLOAD_DIR = Path(os.getenv("UPLOAD_DIR", BASE_DIR / "uploads"))
STATIC_DIR = BASE_DIR / "static"

APP_TITLE = os.getenv("APP_TITLE", "PDF Mini App")
MAX_FILE_SIZE_MB = int(os.getenv("MAX_FILE_SIZE_MB", "80"))
MAX_TOTAL_UPLOAD_MB = int(os.getenv("MAX_TOTAL_UPLOAD_MB", "250"))
FILE_TTL_SECONDS = int(os.getenv("FILE_TTL_SECONDS", str(24 * 3600)))
CLEANUP_ON_REQUEST = os.getenv("CLEANUP_ON_REQUEST", "true").lower() == "true"

ENABLE_BASIC_AUTH = os.getenv("ENABLE_BASIC_AUTH", "false").lower() == "true"
BASIC_AUTH_USER = os.getenv("BASIC_AUTH_USER", "admin")
BASIC_AUTH_PASS = os.getenv("BASIC_AUTH_PASS", "")

USE_S3 = os.getenv("USE_S3", "false").lower() == "true"
S3_BUCKET = os.getenv("S3_BUCKET", "")
S3_ENDPOINT_URL = os.getenv("S3_ENDPOINT_URL", "")
S3_REGION = os.getenv("S3_REGION", "auto")
AWS_ACCESS_KEY_ID = os.getenv("AWS_ACCESS_KEY_ID", "")
AWS_SECRET_ACCESS_KEY = os.getenv("AWS_SECRET_ACCESS_KEY", "")

UPLOAD_DIR.mkdir(parents=True, exist_ok=True)

logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"))
logger = logging.getLogger("pdf-miniapp")

app = FastAPI(title=APP_TITLE)
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")
security = HTTPBasic()

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

def auth_guard(credentials: HTTPBasicCredentials = Depends(security)):
    if not ENABLE_BASIC_AUTH:
        return True
    ok_user = secrets.compare_digest(credentials.username, BASIC_AUTH_USER)
    ok_pass = secrets.compare_digest(credentials.password, BASIC_AUTH_PASS)
    if not (ok_user and ok_pass):
        raise HTTPException(status_code=401, detail="Unauthorized", headers={"WWW-Authenticate": "Basic"})
    return True

def s3_client():
    if not USE_S3:
        return None
    return boto3.client(
        "s3",
        endpoint_url=S3_ENDPOINT_URL or None,
        region_name=S3_REGION,
        aws_access_key_id=AWS_ACCESS_KEY_ID or None,
        aws_secret_access_key=AWS_SECRET_ACCESS_KEY or None,
        config=Config(signature_version="s3v4"),
    )

def object_key(name: str) -> str:
    return f"tmp/{name}"

def store_bytes(name: str, data: bytes):
    if USE_S3:
        client = s3_client()
        client.put_object(Bucket=S3_BUCKET, Key=object_key(name), Body=data)
    else:
        (UPLOAD_DIR / name).write_bytes(data)

def read_bytes(name: str) -> bytes:
    if USE_S3:
        client = s3_client()
        obj = client.get_object(Bucket=S3_BUCKET, Key=object_key(name))
        return obj["Body"].read()
    return (UPLOAD_DIR / name).read_bytes()

def exists(name: str) -> bool:
    if USE_S3:
        client = s3_client()
        try:
            client.head_object(Bucket=S3_BUCKET, Key=object_key(name))
            return True
        except Exception:
            return False
    return (UPLOAD_DIR / name).exists()

def delete_name(name: str):
    if USE_S3:
        client = s3_client()
        try:
            client.delete_object(Bucket=S3_BUCKET, Key=object_key(name))
        except Exception:
            pass
    else:
        (UPLOAD_DIR / name).unlink(missing_ok=True)

def cleanup_local_uploads():
    now = time.time()
    for path in UPLOAD_DIR.iterdir():
        try:
            if now - path.stat().st_mtime > FILE_TTL_SECONDS:
                path.unlink(missing_ok=True)
        except Exception:
            pass

@app.middleware("http")
async def log_and_cleanup(request: Request, call_next):
    start = time.time()
    if CLEANUP_ON_REQUEST and not USE_S3:
        cleanup_local_uploads()
    response = await call_next(request)
    elapsed = round((time.time() - start) * 1000, 1)
    logger.info("%s %s -> %s in %sms", request.method, request.url.path, response.status_code, elapsed)
    response.headers["Cache-Control"] = "no-store"
    response.headers["X-Content-Type-Options"] = "nosniff"
    return response

def validate_upload_sizes(files: List[UploadFile]) -> None:
    total_size = 0
    for file in files:
        size = getattr(file, "size", None)
        if size is not None:
            total_size += size
            if size > MAX_FILE_SIZE_MB * 1024 * 1024:
                raise HTTPException(status_code=413, detail=f"{file.filename} supera il limite di {MAX_FILE_SIZE_MB} MB.")
    if total_size and total_size > MAX_TOTAL_UPLOAD_MB * 1024 * 1024:
        raise HTTPException(status_code=413, detail=f"Upload totale superiore a {MAX_TOTAL_UPLOAD_MB} MB.")

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
    cmd = [
        gs, "-sDEVICE=pdfwrite", "-dCompatibilityLevel=1.4", f"-dPDFSETTINGS={setting}",
        "-dNOPAUSE", "-dQUIET", "-dBATCH", f"-sOutputFile={out_path}", str(input_path),
    ]
    result = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    if result.returncode == 0 and out_path.exists():
        return out_path
    return input_path

@app.get("/health")
def health():
    return JSONResponse({
        "status": "ok",
        "storage": "s3" if USE_S3 else "local",
        "auth": ENABLE_BASIC_AUTH,
        "max_file_size_mb": MAX_FILE_SIZE_MB,
    })

@app.get("/")
def index(_=Depends(auth_guard)):
    return FileResponse(str(STATIC_DIR / "index.html"))

@app.post("/api/upload")
async def upload_pdfs(
    files: List[UploadFile] = File(...),
    passwords_json: str = Form(default="{}"),
    _=Depends(auth_guard),
):
    try:
        passwords = json.loads(passwords_json or "{}")
    except Exception:
        passwords = {}
    if not files:
        raise HTTPException(status_code=400, detail="Carica almeno un PDF.")
    validate_upload_sizes(files)

    all_pages = []
    sources = []

    for file in files:
        if not file.filename.lower().endswith(".pdf"):
            raise HTTPException(status_code=400, detail=f"{file.filename} non è un PDF.")
        pdf_bytes = await file.read()
        password = passwords.get(file.filename, "")
        if len(pdf_bytes) > MAX_FILE_SIZE_MB * 1024 * 1024:
            raise HTTPException(status_code=413, detail=f"{file.filename} supera il limite di {MAX_FILE_SIZE_MB} MB.")
        reader = decrypt_reader_from_bytes(pdf_bytes, password)

        token = str(uuid.uuid4())
        store_bytes(f"{token}.pdf", pdf_bytes)
        store_bytes(f"{token}.pwd", (password or "").encode("utf-8"))

        doc = fitz.open(stream=pdf_bytes, filetype="pdf")
        if doc.needs_pass and not doc.authenticate(password):
            raise HTTPException(status_code=400, detail=f"Password non corretta per {file.filename}.")
        page_count = len(reader.pages)
        sources.append({"token": token, "filename": file.filename, "page_count": page_count})

        for i in range(page_count):
            page = doc.load_page(i)
            pix = page.get_pixmap(matrix=fitz.Matrix(0.22, 0.22), alpha=False)
            b64 = base64.b64encode(pix.tobytes("png")).decode("utf-8")
            rect = page.rect
            all_pages.append({
                "label": f"{file.filename} · Pag. {i+1}",
                "source_name": file.filename,
                "token": token,
                "source_index": i,
                "rotation": 0,
                "is_blank": False,
                "width": rect.width,
                "height": rect.height,
                "thumb": f"data:image/png;base64,{b64}",
            })

    return {"sources": sources, "pages": all_pages, "total_pages": len(all_pages)}

@app.post("/api/export")
def export_pdf(payload: ExportPayload, _=Depends(auth_guard)):
    readers = {}
    writer = PdfWriter()
    for item in payload.items:
        if item.is_blank:
            writer.add_blank_page(width=item.width, height=item.height)
            continue
        if not item.token or item.source_index is None:
            continue
        if item.token not in readers:
            pdf_name = f"{item.token}.pdf"
            pwd_name = f"{item.token}.pwd"
            if not exists(pdf_name):
                raise HTTPException(status_code=404, detail=f"PDF sorgente non trovato: {item.source_name or item.token}")
            data = read_bytes(pdf_name)
            pwd = read_bytes(pwd_name).decode("utf-8") if exists(pwd_name) else ""
            readers[item.token] = decrypt_reader_from_bytes(data, pwd)
        page = readers[item.token].pages[item.source_index]
        if item.rotation:
            page = page.rotate(item.rotation)
        writer.add_page(page)

    out_name = f"export_{uuid.uuid4()}.pdf"
    out_path = UPLOAD_DIR / out_name
    with open(out_path, "wb") as f:
        writer.write(f)
    final_path = compress_pdf_file(out_path, payload.compression)

    if USE_S3:
        store_bytes(final_path.name, final_path.read_bytes())
        final_path.unlink(missing_ok=True)

    return {"download_url": f"/api/download/{final_path.name}"}

@app.get("/api/download/{filename}")
def download_file(filename: str, _=Depends(auth_guard)):
    if USE_S3:
        if not exists(filename):
            raise HTTPException(status_code=404, detail="File non trovato.")
        tmp_path = UPLOAD_DIR / filename
        tmp_path.write_bytes(read_bytes(filename))
        return FileResponse(str(tmp_path), media_type="application/pdf", filename=filename)
    path = UPLOAD_DIR / filename
    if not path.exists():
        raise HTTPException(status_code=404, detail="File non trovato.")
    return FileResponse(str(path), media_type="application/pdf", filename=filename)
