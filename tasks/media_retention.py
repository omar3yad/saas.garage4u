import os
import time
import logging

logger = logging.getLogger(__name__)


async def cleanup_expired_media(
    base_media_dir: str = "media",
    retention_days: int = 90,
) -> dict[str, int]:
    """Periodic worker task that crawls base_media_dir and removes image files
    older than retention_days (90 days default).
    Crucially leaves all database records intact for billing and audit compliance.
    """
    scanned_count = 0
    deleted_count = 0
    now = time.time()
    cutoff_seconds = retention_days * 86400

    if not os.path.exists(base_media_dir):
        return {"scanned_count": 0, "deleted_count": 0}

    abs_base = os.path.abspath(base_media_dir)

    for root, dirs, files in os.walk(base_media_dir, topdown=False):
        for file in files:
            ext = os.path.splitext(file)[1].lower()
            if ext in (".webp", ".jpg", ".jpeg", ".png"):
                scanned_count += 1
                filepath = os.path.join(root, file)
                try:
                    mtime = os.path.getmtime(filepath)
                    if (now - mtime) > cutoff_seconds:
                        os.remove(filepath)
                        deleted_count += 1
                        logger.info("Deleted expired media file: %s", filepath)
                except Exception as e:
                    logger.warning("Failed to inspect/delete file %s: %s", filepath, str(e))

        # Prune empty leaf directories (excluding root base dir)
        if os.path.abspath(root) != abs_base:
            try:
                if not os.listdir(root):
                    os.rmdir(root)
            except Exception:
                pass

    return {
        "scanned_count": scanned_count,
        "deleted_count": deleted_count,
    }


__all__ = ["cleanup_expired_media"]
