import os
import asyncio
from abc import ABC, abstractmethod
from datetime import datetime

class FileTooLargeError(Exception):
    def __init__(self, size: int, max_bytes: int):
        self.size = size
        self.max_bytes = max_bytes
        super().__init__(f"File size {size} exceeds maximum permitted limit {max_bytes} bytes")


class StorageProvider(ABC):
    @abstractmethod
    async def save_image(
        self,
        garage_id: int,
        session_id: int,
        image_type: str,
        data: bytes,
        timestamp: datetime | None = None,
    ) -> str:
        """Saves image bytes and returns the isolated relative path."""
        pass

    @abstractmethod
    async def get_image_path(self, relative_path: str) -> str | None:
        """Returns the absolute file path if the file exists, else None."""
        pass

    @abstractmethod
    async def delete_image(self, relative_path: str) -> bool:
        """Deletes image by relative path. Returns True if deleted."""
        pass


class LocalStorageProvider(StorageProvider):
    def __init__(self, base_dir: str | None = None, max_bytes: int = 1_500_000):
        # Default base_dir is the application root directory
        self.base_dir = os.path.abspath(base_dir or os.getcwd())
        self.max_bytes = max_bytes

    def _sync_write(self, full_path: str, data: bytes) -> None:
        os.makedirs(os.path.dirname(full_path), exist_ok=True)
        with open(full_path, "wb") as f:
            f.write(data)

    def _sync_delete(self, full_path: str) -> bool:
        if os.path.exists(full_path):
            os.remove(full_path)
            return True
        return False

    async def save_image(
        self,
        garage_id: int,
        session_id: int,
        image_type: str,
        data: bytes,
        timestamp: datetime | None = None,
    ) -> str:
        """Saves image in isolated garage path:
        media/garages/{garage_id}/plates/{YYYY}/{MM}/{session_id}_{type}.webp
        Strictly enforces 1.5MB size limit.
        """
        if len(data) > self.max_bytes:
            raise FileTooLargeError(size=len(data), max_bytes=self.max_bytes)

        ts = timestamp or datetime.utcnow()
        clean_type = image_type.strip().lower()
        rel_path = f"media/garages/{garage_id}/plates/{ts.year:04d}/{ts.month:02d}/{session_id}_{clean_type}.webp"
        full_path = os.path.join(self.base_dir, rel_path)

        await asyncio.to_thread(self._sync_write, full_path, data)
        return rel_path

    async def get_image_path(self, relative_path: str) -> str | None:
        full_path = os.path.join(self.base_dir, relative_path)
        if os.path.exists(full_path) and os.path.isfile(full_path):
            return full_path
        return None

    async def delete_image(self, relative_path: str) -> bool:
        full_path = os.path.join(self.base_dir, relative_path)
        return await asyncio.to_thread(self._sync_delete, full_path)


__all__ = [
    "FileTooLargeError",
    "StorageProvider",
    "LocalStorageProvider",
]
