import asyncio
import os
import logging
from collections import deque
from contextlib import asynccontextmanager
from concurrent.futures import ProcessPoolExecutor
from dataclasses import dataclass
from datetime import datetime, timezone
from io import BytesIO
from typing import Any, Dict, List, Optional, Tuple

import httpx
from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel, Field
from PIL import Image


APP_NAME = "paddleocr-service"

logger = logging.getLogger(APP_NAME)

# Expose a small UI-friendly log buffer.
_LOG_BUF: deque = deque(maxlen=int(os.getenv("OCR_LOG_BUFFER", "2000")))
_LOG_SEQ = 0


class _InMemoryLogHandler(logging.Handler):
    def __init__(self, buf: deque) -> None:
        super().__init__()
        self._buf = buf

    def emit(self, record: logging.LogRecord) -> None:
        global _LOG_SEQ
        try:
            msg = self.format(record)
        except Exception:
            msg = record.getMessage()
        _LOG_SEQ += 1
        self._buf.append(
            {
                "n": _LOG_SEQ,
                "ts": datetime.now(timezone.utc).isoformat(),
                "level": record.levelname,
                "msg": msg,
            }
        )


_mem_handler = _InMemoryLogHandler(_LOG_BUF)
_mem_handler.setFormatter(logging.Formatter("%(message)s"))

# Attach to root logger so we also see library logs.
_root_logger = logging.getLogger()
if not any(isinstance(h, _InMemoryLogHandler) for h in _root_logger.handlers):
    _root_logger.addHandler(_mem_handler)


def _truncate(s: str, max_len: int = 500) -> str:
    s2 = (s or "").strip()
    if len(s2) <= max_len:
        return s2
    return s2[: max_len - 3] + "..."


def _sanitize_ocr_text(s: str) -> str:
    """Sanitize OCR text for backend storage.

    Some backends/DBs choke on control characters or non-BMP Unicode (4-byte UTF-8).
    We keep: BMP chars, tab/newline, and normal printable chars.
    """

    if not s:
        return ""

    out: List[str] = []
    for ch in s:
        o = ord(ch)
        # drop non-BMP (e.g. emoji, rare ideographs) to avoid DB utf8 (non-utf8mb4) issues
        if o > 0xFFFF:
            continue
        # keep common whitespace
        if ch in ("\n", "\t"):
            out.append(ch)
            continue
        # drop other control chars
        if o < 32 or o == 127:
            continue
        out.append(ch)
    return "".join(out)


# Request-level limit (keeps sync responses bounded)
MAX_URLS = int(os.getenv("OCR_MAX_URLS", "50"))

# Download limits
MAX_BYTES = int(os.getenv("OCR_MAX_BYTES", str(15 * 1024 * 1024)))
DOWNLOAD_CONCURRENCY = int(os.getenv("OCR_DOWNLOAD_CONCURRENCY", "16"))
CONNECT_TIMEOUT = float(os.getenv("OCR_CONNECT_TIMEOUT", "5"))
READ_TIMEOUT = float(os.getenv("OCR_READ_TIMEOUT", "15"))

# OCR parallelism (multi-process)
OCR_WORKERS = int(os.getenv("OCR_WORKERS", "6"))
OCR_SUBMIT_CONCURRENCY = int(
    os.getenv("OCR_SUBMIT_CONCURRENCY", str(max(1, OCR_WORKERS)))
)
OCR_WORKER_MAX_TASKS = int(os.getenv("OCR_WORKER_MAX_TASKS", "100"))

# Only fallback to the accurate preset on sufficiently large images.
SIZE_GATE = int(os.getenv("OCR_SIZE_GATE", "1200"))

# Background task consumer (pulls OCR work from your backend)
TASK_CONSUMER_ENABLED = os.getenv("OCR_TASK_CONSUMER_ENABLED", "0") == "1"
TASK_BASE_URL = os.getenv("OCR_TASK_BASE_URL", "https://dev.tminos.com").rstrip("/")
TASK_CLAIM_PATH = os.getenv("OCR_TASK_CLAIM_PATH", "/api/ocr/tasks/claim")
TASK_COMPLETE_PATH = os.getenv("OCR_TASK_COMPLETE_PATH", "/api/ocr/tasks/complete")
TASK_POLL_SECS = float(os.getenv("OCR_TASK_POLL_SECS", "2"))
TASK_PUBLIC_IP = os.getenv("OCR_TASK_PUBLIC_IP", "").strip() or None
TASK_MAX_OCR_TEXT_LEN = int(os.getenv("OCR_TASK_MAX_OCR_TEXT_LEN", "8000"))
TASK_EMPTY_OCR_TEXT = os.getenv("OCR_TASK_EMPTY_OCR_TEXT", "...")
TASK_NETWORK_SLEEP_SECS = float(os.getenv("OCR_TASK_NETWORK_SLEEP_SECS", "5"))
TASK_COMPLETE_MAX_RETRIES = int(os.getenv("OCR_TASK_COMPLETE_MAX_RETRIES", "8"))
TASK_COMPLETE_NETWORK_MAX_RETRIES = int(
    os.getenv("OCR_TASK_COMPLETE_NETWORK_MAX_RETRIES", "24")
)


def _is_backend_network_error(e: BaseException) -> bool:
    return isinstance(e, (httpx.TimeoutException, httpx.RequestError))


async def _wait_backend(app: FastAPI) -> None:
    """Sleep-loop until backend host is reachable."""

    while True:
        try:
            r = await app.state.task_http.get(TASK_BASE_URL)
            _ = r.status_code
            app.state.consumer_state["backendOk"] = True
            app.state.consumer_state["lastBackendError"] = None
            return
        except asyncio.CancelledError:
            raise
        except Exception as e:
            app.state.consumer_state["backendOk"] = False
            app.state.consumer_state["lastBackendError"] = _truncate(str(e), 300)
            logger.warning(
                "backend unreachable (%s); sleep %.0fs",
                type(e).__name__,
                TASK_NETWORK_SLEEP_SECS,
            )
            await asyncio.sleep(TASK_NETWORK_SLEEP_SECS)


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
APP_VERSION = "1.0.18"


class DownloadError(RuntimeError):
    def __init__(self, api_error: str, completion_text: Optional[str] = None) -> None:
        super().__init__(api_error)
        self.api_error = api_error
        self.completion_text = completion_text


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

    with Image.open(BytesIO(image_bytes)) as source_im:
        im = source_im.convert("RGB")
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


async def _task_claim(app: FastAPI) -> Optional[Dict[str, Any]]:
    await _wait_backend(app)
    url = f"{TASK_BASE_URL}{TASK_CLAIM_PATH}"
    payload: Optional[Dict[str, Any]] = None
    if TASK_PUBLIC_IP:
        payload = {"publicIp": TASK_PUBLIC_IP}

    if payload is None:
        r = await app.state.task_http.post(url)
    else:
        r = await app.state.task_http.post(url, json=payload)
    r.raise_for_status()
    js = r.json()
    if not js.get("success", False):
        raise RuntimeError(f"claim failed: {js.get('message') or 'unknown'}")
    return js.get("data")


async def _task_complete(
    app: FastAPI,
    task_id: int,
    *,
    ocr_text: Optional[str] = None,
    fail_reason: Optional[str] = None,
) -> None:
    url = f"{TASK_BASE_URL}{TASK_COMPLETE_PATH}"
    await _wait_backend(app)
    if ocr_text is not None:
        cleaned = _sanitize_ocr_text(ocr_text or "")
        if not cleaned.strip():
            cleaned = TASK_EMPTY_OCR_TEXT
        payload = {
            "taskId": int(task_id),
            "ocrText": _truncate(
                cleaned,
                max_len=TASK_MAX_OCR_TEXT_LEN,
            ),
        }
    else:
        payload = {
            "taskId": int(task_id),
            "failReason": _truncate(fail_reason or "ocr failed"),
        }

    r = await app.state.task_http.post(url, json=payload)
    try:
        r.raise_for_status()
    except httpx.HTTPStatusError as e:
        body = ""
        try:
            body = r.text
        except Exception:
            body = ""
        raise RuntimeError(
            f"complete http {r.status_code}: {_truncate(body, 800)}"
        ) from e

    js = r.json()
    if not js.get("success", False):
        raise RuntimeError(f"complete failed: {js.get('message') or 'unknown'}")


async def _task_complete_with_retry(
    app: FastAPI,
    task_id: int,
    *,
    ocr_text: Optional[str] = None,
    fail_reason: Optional[str] = None,
) -> None:
    delay = 2.0
    attempts = 0
    network_attempts = 0
    while True:
        try:
            await _task_complete(
                app, task_id, ocr_text=ocr_text, fail_reason=fail_reason
            )
            return
        except asyncio.CancelledError:
            raise
        except Exception as e:
            if _is_backend_network_error(e):
                network_attempts += 1
                app.state.consumer_state["backendOk"] = False
                app.state.consumer_state["lastBackendError"] = _truncate(str(e), 300)
                if network_attempts >= TASK_COMPLETE_NETWORK_MAX_RETRIES:
                    raise RuntimeError(
                        "task complete gave up after "
                        f"{network_attempts} network retries: {e}"
                    ) from e
                await asyncio.sleep(TASK_NETWORK_SLEEP_SECS)
                continue

            attempts += 1
            if attempts >= TASK_COMPLETE_MAX_RETRIES:
                raise RuntimeError(
                    f"task complete gave up after {attempts} attempts: {e}"
                ) from e

            logger.exception("task complete retry taskId=%s err=%s", task_id, e)
            await asyncio.sleep(delay)
            delay = min(delay * 1.5, 30.0)


def _task_completion_text_for_error(err: BaseException) -> Optional[str]:
    if isinstance(err, DownloadError):
        return err.completion_text
    return None


def _create_ocr_executor() -> ProcessPoolExecutor:
    return ProcessPoolExecutor(max_workers=OCR_WORKERS, initializer=_init_ocr_models)


async def _prewarm_executor(app: FastAPI) -> None:
    try:
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(app.state.executor, _init_ocr_models)
    except Exception:
        logger.exception("failed to prewarm ocr workers")


async def _run_ocr_job(app: FastAPI, data: bytes) -> str:
    async with app.state.ocr_sem:
        loop = asyncio.get_running_loop()
        async with app.state.executor_lock:
            executor = app.state.executor
            app.state.executor_active += 1

        try:
            return await loop.run_in_executor(executor, ocr_image_bytes, data)
        finally:
            recycle_old: Optional[ProcessPoolExecutor] = None
            async with app.state.executor_lock:
                app.state.executor_active -= 1
                app.state.executor_jobs += 1
                should_recycle = (
                    OCR_WORKER_MAX_TASKS > 0
                    and app.state.executor_jobs >= OCR_WORKER_MAX_TASKS
                    and app.state.executor_active == 0
                )
                if should_recycle:
                    recycle_old = app.state.executor
                    app.state.executor = _create_ocr_executor()
                    app.state.executor_jobs = 0

            if recycle_old is not None:
                logger.info(
                    "recycling ocr executor after %s jobs", OCR_WORKER_MAX_TASKS
                )
                await _prewarm_executor(app)
                recycle_old.shutdown(wait=False, cancel_futures=False)


async def _task_consumer_loop(app: FastAPI) -> None:
    logger.info(
        "task consumer enabled base=%s poll=%.2fs",
        TASK_BASE_URL,
        TASK_POLL_SECS,
    )

    while True:
        if app.state.consumer_state.get("paused"):
            app.state.consumer_state["idle"] = True
            app.state.consumer_state["currentTask"] = None
            await asyncio.sleep(TASK_POLL_SECS)
            continue

        task: Optional[Dict[str, Any]] = None
        try:
            task = await _task_claim(app)
        except asyncio.CancelledError:
            raise
        except Exception as e:
            if _is_backend_network_error(e):
                app.state.consumer_state["backendOk"] = False
                app.state.consumer_state["lastBackendError"] = _truncate(str(e), 300)
                await asyncio.sleep(TASK_NETWORK_SLEEP_SECS)
                continue

            logger.exception("task claim failed err=%s", e)
            app.state.consumer_state["idle"] = True
            app.state.consumer_state["currentTask"] = None
            await asyncio.sleep(TASK_POLL_SECS)
            continue

        if not task:
            app.state.consumer_state["idle"] = True
            app.state.consumer_state["currentTask"] = None
            await asyncio.sleep(TASK_POLL_SECS)
            continue

        task_id = task.get("id")
        image_url = task.get("imageUrl")
        spu_id = task.get("spuId")
        image_type = task.get("imageType")

        if not task_id or not image_url:
            logger.error("invalid task payload: %s", task)
            await asyncio.sleep(0)
            continue

        app.state.consumer_state["idle"] = False
        app.state.consumer_state["currentTask"] = {
            "id": int(task_id),
            "spuId": spu_id,
            "imageType": image_type,
            "imageUrl": str(image_url),
            "startedAt": datetime.now(timezone.utc).isoformat(),
        }

        logger.info(
            "claimed taskId=%s spuId=%s imageType=%s", task_id, spu_id, image_type
        )
        try:
            data = await _download(str(image_url))
            app.state.metrics["imagesTotal"] += 1
            text = await _run_ocr_job(app, data)
            await _task_complete_with_retry(app, int(task_id), ocr_text=text or "")
            app.state.consumer_state["stats"]["completed"] += 1
            app.state.metrics["imagesOk"] += 1
            logger.info("completed taskId=%s text_len=%s", task_id, len(text or ""))
        except asyncio.CancelledError:
            raise
        except Exception as e:
            completion_text = _task_completion_text_for_error(e)
            if completion_text is not None:
                logger.warning(
                    "task completed with fallback text taskId=%s text=%s err=%s",
                    task_id,
                    completion_text,
                    e,
                )
                await _task_complete_with_retry(
                    app, int(task_id), ocr_text=completion_text
                )
                app.state.consumer_state["stats"]["completed"] += 1
                app.state.metrics["imagesOk"] += 1
            else:
                app.state.consumer_state["stats"]["failed"] += 1
                app.state.metrics["imagesFail"] += 1
                logger.exception("task failed taskId=%s err=%s", task_id, e)
                await _task_complete_with_retry(app, int(task_id), fail_reason=str(e))
        finally:
            app.state.consumer_state["idle"] = True
            app.state.consumer_state["currentTask"] = None


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
        headers={"User-Agent": f"paddleocr-url-api/{APP_VERSION}"},
    )
    app.state.task_http = httpx.AsyncClient(
        timeout=timeout,
        follow_redirects=True,
        headers={"User-Agent": f"paddleocr-task-consumer/{APP_VERSION}"},
    )
    app.state.download_sem = asyncio.Semaphore(DOWNLOAD_CONCURRENCY)
    app.state.ocr_sem = asyncio.Semaphore(max(1, OCR_SUBMIT_CONCURRENCY))
    app.state.executor_lock = asyncio.Lock()
    app.state.executor_jobs = 0
    app.state.executor_active = 0
    app.state.executor = _create_ocr_executor()
    # Best-effort prewarm to avoid first-request latency inside the process pool.
    await _prewarm_executor(app)

    app.state.started_at = datetime.now(timezone.utc)
    app.state.metrics = {
        "imagesTotal": 0,
        "imagesOk": 0,
        "imagesFail": 0,
    }
    app.state.consumer_state = {
        "enabled": TASK_CONSUMER_ENABLED,
        "paused": False,
        "backendOk": True,
        "lastBackendError": None,
        "idle": True,
        "currentTask": None,
        "stats": {"completed": 0, "failed": 0},
    }
    app.state.consumer_task = None
    if TASK_CONSUMER_ENABLED:
        app.state.consumer_task = asyncio.create_task(_task_consumer_loop(app))
    try:
        yield
    finally:
        consumer_task = getattr(app.state, "consumer_task", None)
        if consumer_task is not None:
            consumer_task.cancel()
            try:
                await consumer_task
            except asyncio.CancelledError:
                pass
            except Exception:
                pass

        await app.state.http.aclose()
        await app.state.task_http.aclose()
        app.state.executor.shutdown(wait=True, cancel_futures=True)


app = FastAPI(title=APP_NAME, lifespan=lifespan)


@app.get("/health")
def health() -> Dict[str, Any]:
    return {"ok": True, "name": APP_NAME}


@app.get("/api/state")
def api_state() -> JSONResponse:
    started_at: datetime = app.state.started_at
    uptime = (datetime.now(timezone.utc) - started_at).total_seconds()
    return JSONResponse(
        {
            "startedAt": started_at.isoformat(),
            "uptimeSeconds": int(uptime),
            "metrics": app.state.metrics,
            "consumer": app.state.consumer_state,
            "config": {
                "taskBaseUrl": TASK_BASE_URL,
                "taskPollSecs": TASK_POLL_SECS,
            },
        }
    )


@app.get("/api/logs")
def api_logs(since: int = 0) -> JSONResponse:
    items = [it for it in list(_LOG_BUF) if int(it.get("n", 0)) > since]
    return JSONResponse({"items": items})


@app.post("/api/consumer/pause")
async def consumer_pause() -> JSONResponse:
    app.state.consumer_state["paused"] = True
    return JSONResponse({"ok": True})


@app.post("/api/consumer/resume")
async def consumer_resume() -> JSONResponse:
    app.state.consumer_state["paused"] = False
    return JSONResponse({"ok": True})


@app.get("/ui", response_class=HTMLResponse)
def ui() -> str:
    html = """<!doctype html>
<html>
<head>
  <meta charset=\"utf-8\" />
  <meta name=\"viewport\" content=\"width=device-width, initial-scale=1\" />
  <title>OCR Worker</title>
  <style>
    :root {
      --bg0:#070A12;
      --bg1:#0B1120;
      --bg2:#121A2E;
      --panel:rgba(15, 23, 42, .78);
      --panel2:rgba(2, 6, 23, .55);
      --panel3:rgba(15, 23, 42, .58);
      --stroke:rgba(148, 163, 184, .18);
      --stroke-strong:rgba(148, 163, 184, .28);
      --text:#E7EEF9;
      --muted:#9FB3D7;
      --soft:#C7D6F2;
      --good:#2DD4BF;
      --warn:#FBBF24;
      --bad:#FB7185;
      --accent:#38BDF8;
      --mono: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, \"Liberation Mono\", \"Courier New\", monospace;
      --serif: Georgia, \"Iowan Old Style\", \"Palatino Linotype\", Palatino, serif;
    }
    * { box-sizing: border-box; }
    body {
      margin:0;
      color:var(--text);
      background:
        radial-gradient(1200px 600px at 15% 10%, rgba(56,189,248,.14), transparent 55%),
        radial-gradient(900px 500px at 85% 15%, rgba(45,212,191,.10), transparent 60%),
        radial-gradient(900px 700px at 40% 110%, rgba(251,191,36,.10), transparent 55%),
        linear-gradient(180deg, var(--bg0), var(--bg1));
      font: 14px/1.45 system-ui, -apple-system, Segoe UI, Helvetica, Arial;
      min-height:100vh;
    }
    header {
      padding: 20px 20px 16px;
      border-bottom:1px solid rgba(148, 163, 184, .14);
      display:flex;
      align-items:flex-start;
      justify-content:space-between;
      gap:16px;
    }
    header h1 {
      margin:0;
      font: 700 20px/1.1 var(--serif);
      letter-spacing:.25px;
    }
    header .sub {
      color:var(--muted);
      font-size:12px;
      margin-top:6px;
    }
    .header-meta {
      display:flex;
      flex-wrap:wrap;
      gap:8px;
      margin-top:14px;
    }
    .pill {
      font-size:12px;
      color:var(--soft);
      padding:6px 10px;
      border:1px solid var(--stroke);
      border-radius:999px;
      background:rgba(2,6,23,.35);
      backdrop-filter: blur(10px);
      white-space:nowrap;
    }
    .pill strong {
      color:var(--text);
      font-weight:700;
    }
    .status-hero {
      min-width: 250px;
      padding: 14px 16px;
      border:1px solid var(--stroke);
      border-radius: 18px;
      background: linear-gradient(180deg, rgba(15,23,42,.78), rgba(2,6,23,.52));
      box-shadow: 0 20px 50px rgba(0,0,0,.28);
      backdrop-filter: blur(14px);
    }
    .status-label {
      color:var(--muted);
      font-size:11px;
      letter-spacing:.16em;
      text-transform:uppercase;
      margin-bottom:8px;
    }
    .status-main {
      display:flex;
      align-items:center;
      gap:10px;
      font-size:17px;
      font-weight:700;
      color:var(--text);
    }
    .status-dot {
      width:10px;
      height:10px;
      border-radius:50%;
      background:var(--muted);
      box-shadow:0 0 0 6px rgba(159,179,215,.12);
      flex:none;
    }
    .status-main.good .status-dot { background:var(--good); box-shadow:0 0 0 6px rgba(45,212,191,.12); }
    .status-main.warn .status-dot { background:var(--warn); box-shadow:0 0 0 6px rgba(251,191,36,.12); }
    .status-main.bad .status-dot { background:var(--bad); box-shadow:0 0 0 6px rgba(251,113,133,.12); }
    .status-detail {
      margin-top:8px;
      color:var(--muted);
      font-size:12px;
      line-height:1.5;
    }
    main {
      display:grid;
      grid-template-columns: minmax(340px, 0.95fr) minmax(420px, 1.35fr);
      gap: 16px;
      padding: 16px;
    }
    .card {
      border:1px solid var(--stroke);
      border-radius: 18px;
      background: linear-gradient(180deg, var(--panel), rgba(8, 15, 31, .82));
      box-shadow: 0 18px 60px rgba(0,0,0,.35);
      overflow:hidden;
      backdrop-filter: blur(16px);
    }
    .card .hd {
      padding: 14px 14px 12px;
      border-bottom:1px solid rgba(148,163,184,.14);
      display:flex;
      align-items:center;
      justify-content:space-between;
      gap:10px;
    }
    .card .hd .t {
      color: var(--muted);
      font-size:12px;
      letter-spacing:.2px;
      text-transform: uppercase;
    }
    .card .bd { padding: 14px; }
    .stack { display:grid; gap:16px; }
    .summary-grid {
      display:grid;
      grid-template-columns: repeat(2, minmax(0, 1fr));
      gap:12px;
      margin-bottom:14px;
    }
    .metric {
      padding: 14px;
      border:1px solid rgba(148,163,184,.14);
      border-radius: 16px;
      background: linear-gradient(180deg, rgba(15,23,42,.55), rgba(15,23,42,.28));
    }
    .metric .k {
      margin-bottom:6px;
      color:var(--muted);
      font-size:12px;
    }
    .metric .num {
      font: 700 24px/1.05 var(--serif);
      letter-spacing:.3px;
      color:var(--text);
    }
    .metric .hint {
      margin-top:6px;
      color:var(--muted);
      font-size:12px;
    }
    .grid { display:grid; grid-template-columns: 1fr; gap:0; }
    .row {
      display:grid;
      grid-template-columns: 120px minmax(0, 1fr);
      align-items:start;
      gap:14px;
      padding: 12px 0;
      border-bottom:1px dashed rgba(148,163,184,.16);
    }
    .row:last-child { border-bottom:none; }
    .k {
      color: var(--muted);
      font-size: 12px;
      line-height: 1.55;
      padding-top: 2px;
    }
    .v {
      color:var(--soft);
      font-family: var(--mono);
      line-height: 1.65;
      min-width: 0;
      white-space: pre-wrap;
      overflow-wrap: anywhere;
      word-break: break-word;
    }
    .v strong { color:var(--text); }
    .value-block {
      display:inline-flex;
      max-width:100%;
      padding:8px 10px;
      border:1px solid rgba(148,163,184,.12);
      border-radius: 12px;
      background: rgba(2,6,23,.24);
      box-shadow: inset 0 1px 0 rgba(255,255,255,.02);
    }
    .value-block.url {
      display:flex;
      align-items:flex-start;
    }
    .value-block.time {
      letter-spacing:.02em;
      white-space:normal;
    }
    .btns { display:flex; gap:8px; flex-wrap:wrap; justify-content:flex-end; align-items:center; }
    button {
      appearance:none;
      border:1px solid var(--stroke);
      background: rgba(2,6,23,.62);
      color: var(--text);
      padding: 9px 12px;
      border-radius: 12px;
      font-weight: 600;
      cursor:pointer;
      transition: border-color .18s ease, background .18s ease, transform .18s ease, opacity .18s ease;
    }
    button:hover { border-color: rgba(148,163,184,.35); background: rgba(15,23,42,.82); }
    button:active { transform: translateY(1px); }
    button[disabled] { opacity:.45; cursor:not-allowed; }
    .btn-primary { border-color: rgba(56,189,248,.3); background: rgba(56,189,248,.14); }
    .btn-primary:hover { border-color: rgba(56,189,248,.45); background: rgba(56,189,248,.2); }
    .btn-warn { border-color: rgba(251,191,36,.28); background: rgba(251,191,36,.12); }
    .btn-warn:hover { border-color: rgba(251,191,36,.4); background: rgba(251,191,36,.18); }
    .task-card {
      padding: 14px;
      border:1px solid rgba(56,189,248,.16);
      border-radius: 16px;
      background:
        linear-gradient(180deg, rgba(56,189,248,.08), transparent 60%),
        rgba(8,15,31,.52);
    }
    .task-head {
      display:flex;
      align-items:flex-start;
      justify-content:space-between;
      gap:12px;
      margin-bottom:12px;
    }
    .task-title {
      font: 700 17px/1.2 var(--serif);
      color:var(--text);
      margin:0;
    }
    .task-sub {
      color:var(--muted);
      font-size:12px;
      margin-top:4px;
    }
    .task-grid {
      display:grid;
      grid-template-columns: repeat(2, minmax(0, 1fr));
      gap:12px;
      margin-bottom:12px;
    }
    .task-field {
      padding:12px;
      border:1px solid rgba(148,163,184,.14);
      border-radius: 14px;
      background: rgba(15,23,42,.4);
    }
    .task-field .label {
      color:var(--muted);
      font-size:11px;
      text-transform:uppercase;
      letter-spacing:.12em;
      margin-bottom:6px;
    }
    .task-field .value {
      color:var(--text);
      font-family:var(--mono);
      line-height:1.65;
      min-width:0;
      white-space:pre-wrap;
      overflow-wrap:anywhere;
      word-break:break-word;
    }
    .task-url {
      padding:12px;
      border-radius:14px;
      background:rgba(2,6,23,.38);
      border:1px solid rgba(148,163,184,.14);
    }
    .task-url .label {
      color:var(--muted);
      font-size:11px;
      text-transform:uppercase;
      letter-spacing:.12em;
      margin-bottom:6px;
    }
    .task-url .value {
      display:block;
      color:var(--soft);
      font-family:var(--mono);
      line-height:1.7;
      white-space:pre-wrap;
      overflow-wrap:anywhere;
      word-break:break-word;
    }
    .task-empty {
      padding:16px;
      border:1px dashed rgba(148,163,184,.2);
      border-radius:16px;
      color:var(--muted);
      background:rgba(2,6,23,.2);
    }
    .good { color: var(--good); }
    .bad { color: var(--bad); }
    .warn { color: var(--warn); }
    .soft { color: var(--muted); }
    .inline-status {
      display:inline-flex;
      align-items:center;
      gap:8px;
      padding:7px 10px;
      border-radius:999px;
      border:1px solid var(--stroke);
      background:rgba(2,6,23,.35);
      font-size:12px;
      color:var(--soft);
    }
    .inline-status::before {
      content:'';
      width:8px;
      height:8px;
      border-radius:50%;
      background:currentColor;
      opacity:.9;
    }
    .action-note {
      min-height:20px;
      color:var(--muted);
      font-size:12px;
      text-align:right;
    }
    .log-toolbar {
      display:flex;
      flex-wrap:wrap;
      align-items:center;
      gap:8px;
    }
    .toggle {
      display:inline-flex;
      align-items:center;
      gap:8px;
      padding:7px 10px;
      border:1px solid var(--stroke);
      border-radius:999px;
      background:rgba(2,6,23,.35);
      color:var(--soft);
      font-size:12px;
      cursor:pointer;
      user-select:none;
    }
    .toggle input { accent-color: var(--accent); }
    .log-meta {
      color:var(--muted);
      font-size:12px;
    }
    pre {
      margin:0;
      padding: 14px;
      height: 68vh;
      overflow:auto;
      background: linear-gradient(180deg, rgba(2,6,23,.58), rgba(2,6,23,.42));
      border:1px solid rgba(148,163,184,.16);
      border-radius: 14px;
      font-family: var(--mono);
      font-size: 12px;
      line-height: 1.55;
      white-space: pre-wrap;
      color:var(--soft);
      tab-size:2;
    }
    .log-placeholder { color:var(--muted); }
    .muted-link {
      text-decoration:none;
      color:var(--soft);
    }
    @media (max-width: 980px) {
      header { flex-direction:column; }
      .status-hero { width:100%; }
      main { grid-template-columns: 1fr; }
      pre { height: 50vh; }
    }
    @media (max-width: 640px) {
      .summary-grid, .task-grid { grid-template-columns: 1fr; }
      .grid { grid-template-columns: 1fr; }
      .btns { justify-content:flex-start; }
    }
  </style>
</head>
<body>
  <header>
    <div>
      <h1>OCR Worker</h1>
      <div class=\"sub\">Auto-claim tasks, run OCR, auto-complete + live logs · Version __APP_VERSION__</div>
      <div class=\"header-meta\">
        <div class=\"pill\"><strong id=\"summaryMode\">连接中...</strong></div>
        <div class=\"pill\">后端 <strong id=\"summaryBackend\">-</strong></div>
        <div class=\"pill\">轮询 <strong id=\"summaryPoll\">-</strong></div>
        <div class=\"pill\">已完成 <strong id=\"consumerCompleted\">0</strong></div>
        <div class=\"pill\">已失败 <strong id=\"consumerFailed\">0</strong></div>
      </div>
    </div>
    <div class=\"status-hero\">
      <div class=\"status-label\">状态总览</div>
      <div class=\"status-main\" id=\"statusMain\"><span class=\"status-dot\"></span><span id=\"statusText\">连接中...</span></div>
      <div class=\"status-detail\" id=\"statusDetail\">正在加载运行状态与日志。</div>
    </div>
  </header>

  <main>
    <section class=\"card\">
      <div class=\"hd\">
        <div class=\"t\">运行状态</div>
        <div class=\"btns\">
          <button id=\"pauseBtn\" class=\"btn-warn\">暂停消费</button>
          <button id=\"resumeBtn\" class=\"btn-primary\">恢复消费</button>
          <a class=\"pill muted-link\" href=\"/docs\" target=\"_blank\">接口文档</a>
        </div>
      </div>
      <div class=\"bd\">
        <div class=\"summary-grid\">
          <div class=\"metric\"><div class=\"k\">运行时长</div><div class=\"num\" id=\"uptime\">-</div><div class=\"hint\">服务启动后持续累计</div></div>
          <div class=\"metric\"><div class=\"k\">图片处理总数</div><div class=\"num\" id=\"imgTotal\">0</div><div class=\"hint\">下载并进入 OCR 的累计次数</div></div>
          <div class=\"metric\"><div class=\"k\">识别成功</div><div class=\"num good\" id=\"imgOk\">0</div><div class=\"hint\">接口与消费任务共用统计</div></div>
          <div class=\"metric\"><div class=\"k\">识别失败</div><div class=\"num bad\" id=\"imgFail\">0</div><div class=\"hint\">下载失败或 OCR 异常</div></div>
        </div>

        <div class=\"stack\">
          <div>
            <div class=\"grid\">
              <div class=\"row\"><div class=\"k\">消费状态</div><div class=\"v\"><span class=\"inline-status\" id=\"consumerBadge\">-</span></div></div>
              <div class=\"row\"><div class=\"k\">网络状态</div><div class=\"v\" id=\"net\">-</div></div>
              <div class=\"row\"><div class=\"k\">后端服务</div><div class=\"v\" id=\"backend\">-</div></div>
              <div class=\"row\"><div class=\"k\">启动时间</div><div class=\"v\" id=\"startedAt\">-</div></div>
            </div>
            <div class=\"action-note\" id=\"actionNote\">控制操作准备就绪。</div>
          </div>

          <div class=\"task-card\">
            <div class=\"task-head\">
              <div>
                <h2 class=\"task-title\">当前任务</h2>
                <div class=\"task-sub\" id=\"taskLead\">等待状态数据…</div>
              </div>
              <div class=\"pill\" id=\"taskStatePill\">无任务</div>
            </div>
            <div id=\"taskPanel\" class=\"task-empty\">当前没有正在处理的任务。</div>
          </div>
        </div>
      </div>
    </section>

    <section class=\"card\">
      <div class=\"hd\">
        <div class=\"t\">日志</div>
        <div class=\"log-toolbar\">
          <label class=\"toggle\"><input type=\"checkbox\" id=\"followToggle\" checked /> 跟随最新</label>
          <div class=\"log-meta\" id=\"logMeta\">等待日志…</div>
        </div>
      </div>
      <div class=\"bd\"><pre id=\"log\" class=\"log-placeholder\">正在加载日志…</pre></div>
    </section>
  </main>

  <script>
    var last = 0;
    var pollTimer = null;
    var actionTimer = null;
    var pollDelay = 1000;
    var lastLogTs = '';
    var hadLogs = false;

    var el = {
      statusMain: document.getElementById('statusMain'),
      statusText: document.getElementById('statusText'),
      statusDetail: document.getElementById('statusDetail'),
      summaryMode: document.getElementById('summaryMode'),
      summaryBackend: document.getElementById('summaryBackend'),
      summaryPoll: document.getElementById('summaryPoll'),
      consumerCompleted: document.getElementById('consumerCompleted'),
      consumerFailed: document.getElementById('consumerFailed'),
      uptime: document.getElementById('uptime'),
      imgTotal: document.getElementById('imgTotal'),
      imgOk: document.getElementById('imgOk'),
      imgFail: document.getElementById('imgFail'),
      consumerBadge: document.getElementById('consumerBadge'),
      net: document.getElementById('net'),
      backend: document.getElementById('backend'),
      startedAt: document.getElementById('startedAt'),
      actionNote: document.getElementById('actionNote'),
      pauseBtn: document.getElementById('pauseBtn'),
      resumeBtn: document.getElementById('resumeBtn'),
      taskLead: document.getElementById('taskLead'),
      taskStatePill: document.getElementById('taskStatePill'),
      taskPanel: document.getElementById('taskPanel'),
      log: document.getElementById('log'),
      logMeta: document.getElementById('logMeta'),
      followToggle: document.getElementById('followToggle')
    };

    function fmtSec(s) {
      var h = Math.floor(s / 3600);
      var m = Math.floor((s % 3600) / 60);
      var ss = Math.floor(s % 60);
      var parts = [];
      if (h) parts.push(String(h) + 'h');
      if (h || m) parts.push(String(m) + 'm');
      parts.push(String(ss) + 's');
      return parts.join(' ');
    }

    function fmtDate(ts) {
      if (!ts) return '-';
      var d = new Date(ts);
      if (isNaN(d.getTime())) return ts;
      return d.toLocaleString('zh-CN');
    }

    function escapeHtml(value) {
      var safe = value == null ? '' : String(value);
      return safe.replace(/[&<>\"]/g, function (ch) {
        if (ch === '&') return '&amp;';
        if (ch === '<') return '&lt;';
        if (ch === '>') return '&gt;';
        if (ch === '"') return '&quot;';
        return ch;
      });
    }

    function modeInfo(c) {
      var enabled = !!(c && c.enabled);
      var paused = !!(c && c.paused);
      var idle = !!(c && c.idle);
      var backendOk = c && c.backendOk !== undefined ? !!c.backendOk : true;
      if (!enabled) return { key: 'api-only', label: '仅接口模式', detail: '未启用任务消费器，仅提供 OCR 接口。', tone: backendOk ? '' : 'bad' };
      if (!backendOk) return { key: 'network', label: '网络异常', detail: c.lastBackendError ? ('与后端通信失败：' + c.lastBackendError) : '与后端通信失败，请检查网络或后端服务。', tone: 'bad' };
      if (paused) return { key: 'paused', label: '已暂停', detail: '消费器已暂停，不会继续领取新任务。', tone: 'warn' };
      if (idle) return { key: 'idle', label: '空闲待命', detail: '消费器在线，正在等待可领取的新任务。', tone: '' };
      return { key: 'working', label: '执行中', detail: '正在处理已领取任务，完成后会自动回传结果。', tone: 'good' };
    }

    function setActionNote(text, tone) {
      tone = tone || '';
      el.actionNote.textContent = text;
      el.actionNote.className = ('action-note ' + tone).trim();
      if (actionTimer) clearTimeout(actionTimer);
      actionTimer = setTimeout(function () {
        el.actionNote.textContent = '控制操作准备就绪。';
        el.actionNote.className = 'action-note';
      }, 3200);
    }

    function setButtons(c) {
      var enabled = !!(c && c.enabled);
      var paused = !!(c && c.paused);
      el.pauseBtn.disabled = !enabled || paused;
      el.resumeBtn.disabled = !enabled || !paused;
    }

    function renderTask(c) {
      var task = c && c.currentTask;
      var info = modeInfo(c || {});
      if (!task) {
        el.taskLead.textContent = info.key === 'working' ? '任务状态同步中…' : '当前没有正在执行的消费任务。';
        el.taskStatePill.textContent = info.key === 'paused' ? '已暂停' : (info.key === 'idle' ? '待命中' : (info.key === 'api-only' ? '未启用' : '无任务'));
        el.taskStatePill.className = ('pill ' + info.tone).trim();
        el.taskPanel.className = 'task-empty';
        el.taskPanel.textContent = info.key === 'paused' ? '消费已暂停。恢复后会继续领取新任务。' : '当前没有正在处理的任务。';
        return;
      }
      el.taskLead.textContent = '已领取任务，以下信息会随着状态轮询实时刷新。';
      el.taskStatePill.textContent = '处理中';
      el.taskStatePill.className = 'pill good';
      el.taskPanel.className = '';
      el.taskPanel.innerHTML = '' +
        '<div class="task-grid">' +
          '<div class="task-field"><div class="label">任务 ID</div><div class="value">' + escapeHtml(task.id) + '</div></div>' +
          '<div class="task-field"><div class="label">开始时间</div><div class="value value-block time">' + escapeHtml(fmtDate(task.startedAt)) + '</div></div>' +
          '<div class="task-field"><div class="label">SPU ID</div><div class="value">' + escapeHtml(task.spuId == null ? '-' : task.spuId) + '</div></div>' +
          '<div class="task-field"><div class="label">图片类型</div><div class="value">' + escapeHtml(task.imageType == null ? '-' : task.imageType) + '</div></div>' +
        '</div>' +
        '<div class="task-url"><div class="label">图片地址</div><div class="value value-block url">' + escapeHtml(task.imageUrl || '-') + '</div></div>';
    }

    function appendLogs(items) {
      var i;
      if (last === 0) {
        el.log.textContent = '';
        el.log.classList.remove('log-placeholder');
      }
      for (i = 0; i < items.length; i += 1) {
        var it = items[i];
        var level = String(it.level || '');
        while (level.length < 5) level += ' ';
        el.log.textContent += '[' + it.ts + '] ' + level + ' ' + it.msg + '\\n';
        last = it.n;
        lastLogTs = it.ts || lastLogTs;
      }
      hadLogs = true;
      if (el.followToggle.checked) {
        el.log.scrollTop = el.log.scrollHeight;
      }
    }

    function xhrJson(method, url, done) {
      var xhr = new XMLHttpRequest();
      xhr.open(method, url, true);
      xhr.onreadystatechange = function () {
        if (xhr.readyState !== 4) return;
        if (xhr.status >= 200 && xhr.status < 300) {
          try {
            done(null, JSON.parse(xhr.responseText));
          } catch (err) {
            done(err);
          }
        } else {
          done(new Error('http ' + xhr.status));
        }
      };
      xhr.onerror = function () {
        done(new Error('network_error'));
      };
      xhr.send(null);
    }

    function postAction(path, pendingText, successText) {
      setActionNote(pendingText, 'soft');
      el.pauseBtn.disabled = true;
      el.resumeBtn.disabled = true;
      xhrJson('POST', path, function (err) {
        if (err) {
          setActionNote('操作失败：' + (err && err.message ? err.message : '未知错误'), 'bad');
          return;
        }
        setActionNote(successText, 'good');
        pollDelay = 200;
        scheduleNextPoll(0);
      });
    }

    el.pauseBtn.onclick = function () {
      postAction('/api/consumer/pause', '正在发送暂停指令…', '已发送暂停指令，等待状态确认。');
    };
    el.resumeBtn.onclick = function () {
      postAction('/api/consumer/resume', '正在发送恢复指令…', '已发送恢复指令，等待重新开始消费。');
    };

    function scheduleNextPoll(delay) {
      if (pollTimer) clearTimeout(pollTimer);
      pollTimer = setTimeout(tick, delay);
    }

    function updateState(s) {
      var c = s.consumer || {};
      var stats = c.stats || {};
      var info = modeInfo(c);
      var backendOk = c.backendOk === undefined ? true : !!c.backendOk;
      el.uptime.textContent = fmtSec(s.uptimeSeconds || 0);
      el.imgTotal.textContent = s.metrics.imagesTotal;
      el.imgOk.textContent = s.metrics.imagesOk;
      el.imgFail.textContent = s.metrics.imagesFail;
      el.summaryMode.textContent = info.label;
      el.summaryBackend.textContent = backendOk ? '正常' : '异常';
      el.summaryPoll.textContent = String(s.config.taskPollSecs) + 's';
      el.consumerCompleted.textContent = stats.completed == null ? 0 : stats.completed;
      el.consumerFailed.textContent = stats.failed == null ? 0 : stats.failed;
      el.statusMain.className = ('status-main ' + info.tone).trim();
      el.statusText.textContent = info.label;
      el.statusDetail.textContent = info.detail;
      el.consumerBadge.textContent = info.label;
      el.consumerBadge.className = ('inline-status ' + info.tone).trim();
      el.net.textContent = backendOk ? '正常' : ('异常：' + (c.lastBackendError || '无法连接后端'));
      el.backend.innerHTML = '<span class="value-block url">' + escapeHtml(s.config.taskBaseUrl + '（轮询 ' + s.config.taskPollSecs + 's）') + '</span>';
      el.startedAt.innerHTML = '<span class="value-block time">' + escapeHtml(fmtDate(s.startedAt)) + '</span>';
      setButtons(c);
      renderTask(c);
    }

    function updateLogs(logs) {
      if (logs.items && logs.items.length) {
        appendLogs(logs.items);
      } else if (last === 0) {
        el.log.textContent = '暂无日志输出。';
        el.log.classList.add('log-placeholder');
      }

      if (hadLogs) {
        el.logMeta.textContent = '日志条目已同步至 #' + last + (lastLogTs ? (' · 最新时间 ' + fmtDate(lastLogTs)) : '');
      } else {
        el.logMeta.textContent = '暂无日志输出';
      }
    }

    function handleUiError() {
      el.statusMain.className = 'status-main bad';
      el.statusText.textContent = '连接失败';
      el.statusDetail.textContent = '状态或日志加载失败，系统会自动重试。';
      el.summaryMode.textContent = '连接失败';
      el.summaryBackend.textContent = '异常';
      el.logMeta.textContent = '读取失败，准备重试…';
      if (last === 0) {
        el.log.textContent = '状态或日志加载失败，稍后自动重试。';
        el.log.classList.add('log-placeholder');
      }
      pollDelay = 1800;
      scheduleNextPoll(pollDelay);
    }

    function tick() {
      xhrJson('GET', '/api/state', function (err, stateData) {
        if (err) {
          handleUiError();
          return;
        }

        updateState(stateData);
        xhrJson('GET', '/api/logs?since=' + last, function (logErr, logData) {
          if (logErr) {
            handleUiError();
            return;
          }
          updateLogs(logData);
          pollDelay = 1000;
          scheduleNextPoll(pollDelay);
        });
      });
    }

    tick();
  </script>
</body>
</html>"""
    return html.replace("__APP_VERSION__", APP_VERSION)


async def _download(url: str) -> bytes:
    if not (url.startswith("http://") or url.startswith("https://")):
        raise ValueError("invalid_url")

    async with app.state.download_sem:
        try:
            async with app.state.http.stream("GET", url) as r:
                if r.status_code < 200 or r.status_code >= 300:
                    completion_text = "ERROR_download_failed"
                    if r.status_code == 403:
                        completion_text = "ERROR_403"
                    elif r.status_code == 404:
                        completion_text = "ERROR_404"
                    raise DownloadError("download_failed", completion_text)

                content_length = r.headers.get("Content-Length")
                if content_length:
                    try:
                        if int(content_length) > MAX_BYTES:
                            raise DownloadError("too_large")
                    except ValueError:
                        pass

                total = 0
                chunks: List[bytes] = []
                async for chunk in r.aiter_bytes():
                    if not chunk:
                        continue
                    total += len(chunk)
                    if total > MAX_BYTES:
                        raise DownloadError("too_large")
                    chunks.append(chunk)
                return b"".join(chunks)
        except httpx.TimeoutException as e:
            raise DownloadError("timeout", "ERROR_TIMEOUT") from e
        except DownloadError:
            raise
        except RuntimeError:
            raise
        except Exception as e:
            raise DownloadError("download_failed", "ERROR_download_failed") from e


@app.post("/ocr", response_model=OCRResponse)
async def ocr(req: OCRRequest) -> OCRResponse:
    if len(req.urls) > MAX_URLS:
        raise HTTPException(status_code=400, detail=f"too_many_urls: max {MAX_URLS}")

    results: List[Optional[OCRResult]] = [None] * len(req.urls)
    loop = asyncio.get_running_loop()

    async def handle_one(i: int, url: str) -> None:
        try:
            data = await _download(url)
            app.state.metrics["imagesTotal"] += 1
        except ValueError:
            results[i] = OCRResult(url=url, error="invalid_url")
            app.state.metrics["imagesFail"] += 1
            return
        except RuntimeError as e:
            results[i] = OCRResult(url=url, error=str(e))
            app.state.metrics["imagesFail"] += 1
            return
        except Exception:
            results[i] = OCRResult(url=url, error="download_failed")
            app.state.metrics["imagesFail"] += 1
            return

        try:
            text = await _run_ocr_job(app, data)
            # "no text" is a success case (empty string)
            results[i] = OCRResult(url=url, text=text or "")
            app.state.metrics["imagesOk"] += 1
        except Exception:
            logger.exception("ocr_failed url=%s", url)
            results[i] = OCRResult(url=url, error="ocr_failed")
            app.state.metrics["imagesFail"] += 1

    await asyncio.gather(*[handle_one(i, u) for i, u in enumerate(req.urls)])
    return OCRResponse(
        results=[
            r if r is not None else OCRResult(url=req.urls[i], error="unknown")
            for i, r in enumerate(results)
        ]
    )
