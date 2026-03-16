import asyncio
import os
import logging
from contextlib import asynccontextmanager
from concurrent.futures import ProcessPoolExecutor
from dataclasses import dataclass
from io import BytesIO
from typing import Any, Dict, List, Optional, Tuple

import httpx
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field
from PIL import Image


APP_NAME = "paddleocr-service"

logger = logging.getLogger(APP_NAME)

# Request-level limit (keeps sync responses bounded)
MAX_URLS = int(os.getenv("OCR_MAX_URLS", "50"))

# Download limits
MAX_BYTES = int(os.getenv("OCR_MAX_BYTES", str(15 * 1024 * 1024)))
DOWNLOAD_CONCURRENCY = int(os.getenv("OCR_DOWNLOAD_CONCURRENCY", "16"))
CONNECT_TIMEOUT = float(os.getenv("OCR_CONNECT_TIMEOUT", "5"))
READ_TIMEOUT = float(os.getenv("OCR_READ_TIMEOUT", "15"))

# OCR parallelism (multi-process)
OCR_WORKERS = int(os.getenv("OCR_WORKERS", "6"))

# Only fallback to the accurate preset on sufficiently large images.
SIZE_GATE = int(os.getenv("OCR_SIZE_GATE", "1200"))


class OCRRequest(BaseModel):
    urls: List[str] = Field(..., min_length=1)


class OCRResult(BaseModel):
    url: str
    text: str = ""
    error: Optional[str] = None


class OCRResponse(BaseModel):
    results: List[OCRResult]


@dataclass(frozen=True)
class _Line:
    y: float
    x: float
    score: float
    text: str


_OCR_FAST = None
_OCR_ACCURATE = None


def _init_ocr_models() -> None:
    # Imports happen inside the worker process.
    from paddleocr import PaddleOCR

    global _OCR_FAST, _OCR_ACCURATE

    # Fast-ish config (poster/screenshot friendly)
    _OCR_FAST = PaddleOCR(
        lang="ch",
        use_textline_orientation=False,
        text_det_limit_side_len=1280,
        text_det_box_thresh=0.50,
        text_det_thresh=0.30,
        text_rec_score_thresh=0.35,
    )

    # Accurate config (packaging photo / complex background / small text)
    _OCR_ACCURATE = PaddleOCR(
        lang="ch",
        use_textline_orientation=True,
        text_det_limit_side_len=1536,
        text_det_box_thresh=0.45,
        text_det_thresh=0.25,
        text_det_unclip_ratio=2.0,
        text_rec_score_thresh=0.30,
    )


def _extract_lines(result: Any) -> List[Any]:
    # PaddleOCR (pip) has multiple output shapes depending on version.
    # v2: [[(box,(text,score)), ...]]
    # v3: [{"rec_texts": [...], "rec_scores": [...], "rec_polys": [...], ...}]
    if not result:
        return []
    if isinstance(result, list) and len(result) == 1 and isinstance(result[0], list):
        return result[0]
    if isinstance(result, list) and len(result) == 1 and isinstance(result[0], dict):
        return [result[0]]
    if isinstance(result, list):
        return result
    return []


def _lines_to_text(lines: List[Any]) -> str:
    parsed: List[_Line] = []

    # v3 dict output
    if len(lines) == 1 and isinstance(lines[0], dict):
        d = lines[0]
        texts = d.get("rec_texts") or []
        scores = d.get("rec_scores") or []
        polys = d.get("rec_polys") or d.get("dt_polys") or []
        n = min(len(texts), len(scores), len(polys))

        for i in range(n):
            text = str(texts[i] or "")
            if not text.strip():
                continue
            try:
                score_f = float(scores[i])
            except Exception:
                score_f = 0.0
            try:
                poly = polys[i]
                ys = [float(p[1]) for p in poly]
                xs = [float(p[0]) for p in poly]
                y = min(ys)
                x = min(xs)
            except Exception:
                y, x = 0.0, float(i)

            parsed.append(_Line(y=y, x=x, score=score_f, text=text))

        parsed.sort(key=lambda it: (it.y, it.x))
        return "\n".join([it.text for it in parsed]).strip()

    for line in lines:
        try:
            box, (text, score) = line
            if not text:
                continue
            score_f = float(score)
            ys = [p[1] for p in box]
            xs = [p[0] for p in box]
            parsed.append(
                _Line(y=float(min(ys)), x=float(min(xs)), score=score_f, text=str(text))
            )
        except Exception:
            continue

    parsed.sort(key=lambda it: (it.y, it.x))
    return "\n".join([it.text for it in parsed]).strip()


def _fallback_allowed(size: Tuple[int, int]) -> bool:
    w, h = size
    return min(w, h) >= SIZE_GATE


def _should_fallback(size: Tuple[int, int], lines: List[Any]) -> bool:
    if not _fallback_allowed(size):
        return False

    # v3 dict output
    if len(lines) == 1 and isinstance(lines[0], dict):
        d = lines[0]
        texts = [str(t or "").strip() for t in (d.get("rec_texts") or [])]
        scores_raw = d.get("rec_scores") or []

        scores: List[float] = []
        for t, s in zip(texts, scores_raw):
            if not t:
                continue
            try:
                scores.append(float(s))
            except Exception:
                scores.append(0.0)

        if not scores:
            return True

        avg = sum(scores) / len(scores)
        mx = max(scores)
        if avg < 0.50 and len(scores) < 5:
            return True
        if mx < 0.70 and len(scores) < 3:
            return True
        return False

    if len(lines) == 0:
        return True

    scores: List[float] = []
    for line in lines:
        try:
            scores.append(float(line[1][1]))
        except Exception:
            pass
    if not scores:
        return True

    avg = sum(scores) / len(scores)
    mx = max(scores)

    # Size-gated heuristic tuned for recall-first (avoid unnecessary 2-pass on small product images)
    if avg < 0.50 and len(lines) < 5:
        return True
    if mx < 0.70 and len(lines) < 3:
        return True
    return False


def ocr_image_bytes(image_bytes: bytes) -> str:
    # Worker process entrypoint.
    global _OCR_FAST, _OCR_ACCURATE
    if _OCR_FAST is None or _OCR_ACCURATE is None:
        _init_ocr_models()

    im = Image.open(BytesIO(image_bytes)).convert("RGB")
    w, h = im.size
    import numpy as np

    arr = np.array(im)

    fast_res = _OCR_FAST.predict(arr, use_textline_orientation=False)
    fast_lines = _extract_lines(fast_res)
    fast_text = _lines_to_text(fast_lines)

    if _should_fallback((w, h), fast_lines):
        acc_res = _OCR_ACCURATE.predict(arr, use_textline_orientation=True)
        acc_lines = _extract_lines(acc_res)
        return _lines_to_text(acc_lines)

    return fast_text


@asynccontextmanager
async def lifespan(app: FastAPI):
    timeout = httpx.Timeout(
        connect=CONNECT_TIMEOUT,
        read=READ_TIMEOUT,
        write=READ_TIMEOUT,
        pool=READ_TIMEOUT,
    )
    app.state.http = httpx.AsyncClient(
        timeout=timeout,
        follow_redirects=True,
        headers={"User-Agent": "paddleocr-url-api/1.0"},
    )
    app.state.download_sem = asyncio.Semaphore(DOWNLOAD_CONCURRENCY)
    app.state.executor = ProcessPoolExecutor(
        max_workers=OCR_WORKERS, initializer=_init_ocr_models
    )
    # Best-effort prewarm to avoid first-request latency inside the process pool.
    try:
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(app.state.executor, _init_ocr_models)
    except Exception:
        logger.exception("failed to prewarm ocr workers")
    try:
        yield
    finally:
        await app.state.http.aclose()
        app.state.executor.shutdown(wait=True, cancel_futures=True)


app = FastAPI(title=APP_NAME, lifespan=lifespan)


@app.get("/health")
def health() -> Dict[str, Any]:
    return {"ok": True, "name": APP_NAME}


async def _download(url: str) -> bytes:
    if not (url.startswith("http://") or url.startswith("https://")):
        raise ValueError("invalid_url")

    async with app.state.download_sem:
        try:
            async with app.state.http.stream("GET", url) as r:
                if r.status_code < 200 or r.status_code >= 300:
                    raise RuntimeError("download_failed")

                content_length = r.headers.get("Content-Length")
                if content_length:
                    try:
                        if int(content_length) > MAX_BYTES:
                            raise RuntimeError("too_large")
                    except ValueError:
                        pass

                total = 0
                chunks: List[bytes] = []
                async for chunk in r.aiter_bytes():
                    if not chunk:
                        continue
                    total += len(chunk)
                    if total > MAX_BYTES:
                        raise RuntimeError("too_large")
                    chunks.append(chunk)
                return b"".join(chunks)
        except httpx.TimeoutException as e:
            raise RuntimeError("timeout") from e
        except RuntimeError:
            raise
        except Exception as e:
            raise RuntimeError("download_failed") from e


@app.post("/ocr", response_model=OCRResponse)
async def ocr(req: OCRRequest) -> OCRResponse:
    if len(req.urls) > MAX_URLS:
        raise HTTPException(status_code=400, detail=f"too_many_urls: max {MAX_URLS}")

    results: List[Optional[OCRResult]] = [None] * len(req.urls)
    loop = asyncio.get_running_loop()

    async def handle_one(i: int, url: str) -> None:
        try:
            data = await _download(url)
        except ValueError:
            results[i] = OCRResult(url=url, error="invalid_url")
            return
        except RuntimeError as e:
            results[i] = OCRResult(url=url, error=str(e))
            return
        except Exception:
            results[i] = OCRResult(url=url, error="download_failed")
            return

        try:
            text = await loop.run_in_executor(app.state.executor, ocr_image_bytes, data)
            # "no text" is a success case (empty string)
            results[i] = OCRResult(url=url, text=text or "")
        except Exception:
            logger.exception("ocr_failed url=%s", url)
            results[i] = OCRResult(url=url, error="ocr_failed")

    await asyncio.gather(*[handle_one(i, u) for i, u in enumerate(req.urls)])
    return OCRResponse(
        results=[
            r if r is not None else OCRResult(url=req.urls[i], error="unknown")
            for i, r in enumerate(results)
        ]
    )
