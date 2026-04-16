"""Windows entrypoint for the URL OCR API.

This file exists to make PyInstaller packaging and Windows Service hosting easier.
"""

from __future__ import annotations

import multiprocessing
import os


def main() -> None:
    # Divert frozen multiprocessing child processes before importing the
    # FastAPI app or Uvicorn stack again.
    multiprocessing.freeze_support()

    import uvicorn

    # Ensure PyInstaller includes the FastAPI app module.
    import app  # noqa: F401

    host = os.getenv("OCR_HOST", "0.0.0.0")
    port = int(os.getenv("OCR_PORT", "8000"))

    # IMPORTANT: keep uvicorn workers = 1.
    # The app itself uses a ProcessPoolExecutor for OCR parallelism.
    uvicorn.run(
        "app:app",
        host=host,
        port=port,
        workers=1,
        log_level=os.getenv("UVICORN_LOG_LEVEL", "info"),
    )


if __name__ == "__main__":
    main()
