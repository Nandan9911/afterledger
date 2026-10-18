"""Papers, page images and filled forms in a Cloud Storage bucket.

Refs: 'gs:<path>' for what is written here. 'local:<path>' refs (made by the local store, as in the demo
snapshots) resolve to the same path in the bucket, so a case moves between stores unchanged. 'data:<path>'
refs are fixtures shipped in the image and are read from the data directory.
"""
from __future__ import annotations

import asyncio
import mimetypes
from pathlib import Path
from typing import Any


class GcsBlobStore:
    def __init__(self, bucket: str, data_dir: Path, client: Any = None):
        if client is None:
            from google.cloud import storage
            client = storage.Client()
        self.client = client
        self.bucket = client.bucket(bucket)
        self.data_dir = data_dir

    def _data_path(self, rel: str) -> Path:
        p = (self.data_dir / rel).resolve()
        if not str(p).startswith(str(self.data_dir.resolve())):
            raise ValueError("blob path escapes its root")
        return p

    @staticmethod
    def _object(ref: str) -> str:
        scheme, _, rel = ref.partition(":")
        if scheme not in ("gs", "local") or not rel or ".." in Path(rel).parts:
            raise ValueError(f"not a bucket ref: {ref}")
        return rel

    async def put(self, path: str, data: bytes, content_type: str = "application/octet-stream") -> str:
        blob = self.bucket.blob(path)
        await asyncio.to_thread(blob.upload_from_string, data, content_type=content_type)
        return f"gs:{path}"

    async def get(self, ref: str) -> bytes:
        if ref.startswith("data:"):
            return self._data_path(ref.partition(":")[2]).read_bytes()
        blob = self.bucket.blob(self._object(ref))
        return await asyncio.to_thread(blob.download_as_bytes)

    async def exists(self, ref: str) -> bool:
        try:
            if ref.startswith("data:"):
                return self._data_path(ref.partition(":")[2]).exists()
            return await asyncio.to_thread(self.bucket.blob(self._object(ref)).exists)
        except ValueError:
            return False

    def content_type(self, ref: str) -> str:
        return mimetypes.guess_type(ref)[0] or "application/octet-stream"

    async def delete_prefix(self, prefix: str) -> int:
        """Removes every object under a prefix (a demo case's files, before it is restored)."""
        if not prefix or ".." in Path(prefix).parts:
            raise ValueError(f"not a prefix to delete: {prefix!r}")

        def go() -> int:
            blobs = list(self.bucket.list_blobs(prefix=prefix))
            if blobs:
                self.bucket.delete_blobs(blobs, on_error=lambda _b: None)
            return len(blobs)

        return await asyncio.to_thread(go)
