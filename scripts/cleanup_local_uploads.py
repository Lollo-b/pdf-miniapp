import os
import time
from pathlib import Path

upload_dir = Path(os.getenv("UPLOAD_DIR", "./uploads"))
ttl = int(os.getenv("FILE_TTL_SECONDS", str(24 * 3600)))
now = time.time()

if upload_dir.exists():
    for path in upload_dir.iterdir():
        try:
            if now - path.stat().st_mtime > ttl:
                path.unlink(missing_ok=True)
                print(f"deleted {path.name}")
        except Exception as e:
            print(f"skip {path}: {e}")
