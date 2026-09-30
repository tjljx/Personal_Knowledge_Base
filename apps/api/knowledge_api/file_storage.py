"""Content-addressed, immutable local file storage."""

import hashlib
import mimetypes
import os
import tempfile
import zipfile
from pathlib import Path

from fastapi import HTTPException, UploadFile

from knowledge_api.config import get_settings

ALLOWED_EXTENSIONS = {
    ".pdf",
    ".doc",
    ".docx",
    ".ppt",
    ".pptx",
    ".xls",
    ".xlsx",
    ".md",
    ".html",
    ".htm",
    ".png",
    ".jpg",
    ".jpeg",
    ".tif",
    ".tiff",
    ".webp",
}
ZIP_MARKERS = {".docx": "word/", ".pptx": "ppt/", ".xlsx": "xl/"}
OLE_SIGNATURE = bytes.fromhex("d0cf11e0a1b11ae1")


def blob_path(digest: str) -> Path:
    return get_settings().storage_root / "blobs" / digest[:2] / digest


def _check_signature(path: Path, extension: str) -> None:
    with path.open("rb") as handle:
        header = handle.read(16)
    valid = True
    if extension == ".pdf":
        valid = header.startswith(b"%PDF-")
    elif extension in ZIP_MARKERS:
        try:
            with zipfile.ZipFile(path) as archive:
                names = archive.namelist()
                valid = "[Content_Types].xml" in names and any(
                    name.startswith(ZIP_MARKERS[extension]) for name in names
                )
        except (OSError, zipfile.BadZipFile):
            valid = False
    elif extension in {".doc", ".ppt", ".xls"}:
        valid = header.startswith(OLE_SIGNATURE)
    elif extension == ".png":
        valid = header.startswith(b"\x89PNG\r\n\x1a\n")
    elif extension in {".jpg", ".jpeg"}:
        valid = header.startswith(b"\xff\xd8\xff")
    elif extension in {".tif", ".tiff"}:
        valid = header.startswith((b"II*\x00", b"MM\x00*"))
    elif extension == ".webp":
        valid = header.startswith(b"RIFF") and header[8:12] == b"WEBP"
    else:
        try:
            path.read_text(encoding="utf-8")
            valid = b"\x00" not in header
        except UnicodeError:
            valid = False
    if not valid:
        raise HTTPException(status_code=422, detail="文件内容与扩展名不匹配")


def save_upload(file: UploadFile) -> tuple[str, str, str, int]:
    filename = (file.filename or "").replace("\\", "/").split("/")[-1].strip()
    extension = Path(filename).suffix.lower()
    if not filename or len(filename) > 255 or extension not in ALLOWED_EXTENSIONS:
        raise HTTPException(status_code=422, detail="不支持的文件名或文件类型")
    root = get_settings().storage_root
    staging = root / "staging"
    staging.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(dir=staging, delete=False) as handle:
            temporary = Path(handle.name)
            digest = hashlib.sha256()
            size = 0
            while chunk := file.file.read(1024 * 1024):
                size += len(chunk)
                if size > get_settings().max_upload_bytes:
                    raise HTTPException(status_code=413, detail="文件超过上传大小限制")
                digest.update(chunk)
                handle.write(chunk)
        if size == 0:
            raise HTTPException(status_code=422, detail="不能上传空文件")
        _check_signature(temporary, extension)
        checksum = digest.hexdigest()
        destination = blob_path(checksum)
        destination.parent.mkdir(parents=True, exist_ok=True)
        try:
            os.link(temporary, destination)
        except FileExistsError:
            if destination.stat().st_size != size:
                raise RuntimeError("已有哈希文件大小不一致") from None
        return (
            filename,
            mimetypes.guess_type(filename)[0] or "application/octet-stream",
            checksum,
            size,
        )
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
