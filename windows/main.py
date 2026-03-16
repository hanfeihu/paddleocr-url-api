"""Windows entrypoint for the URL OCR API.

This file exists to make PyInstaller packaging and Windows Service hosting easier.
"""

from __future__ import annotations

import multiprocessing
import os

import uvicorn


def main() -> None:
    # Required for multiprocessing when running as a frozen executable.
    multiprocessing.freeze_support()

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
