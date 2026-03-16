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

# Background task consumer (pulls OCR work from your backend)
TASK_CONSUMER_ENABLED = os.getenv("OCR_TASK_CONSUMER_ENABLED", "0") == "1"
TASK_BASE_URL = os.getenv("OCR_TASK_BASE_URL", "https://dev.tminos.com").rstrip("/")
TASK_CLAIM_PATH = os.getenv("OCR_TASK_CLAIM_PATH", "/api/ocr/tasks/claim")
TASK_COMPLETE_PATH = os.getenv("OCR_TASK_COMPLETE_PATH", "/api/ocr/tasks/complete")
TASK_POLL_SECS = float(os.getenv("OCR_TASK_POLL_SECS", "2"))
TASK_PUBLIC_IP = os.getenv("OCR_TASK_PUBLIC_IP", "").strip() or None


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


async def _task_claim(app: FastAPI) -> Optional[Dict[str, Any]]:
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
    if ocr_text is not None:
        payload = {"taskId": int(task_id), "ocrText": ocr_text}
    else:
        payload = {
            "taskId": int(task_id),
            "failReason": _truncate(fail_reason or "ocr failed"),
        }

    r = await app.state.task_http.post(url, json=payload)
    r.raise_for_status()
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
    while True:
        try:
            await _task_complete(
                app, task_id, ocr_text=ocr_text, fail_reason=fail_reason
            )
            return
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.exception("task complete retry taskId=%s err=%s", task_id, e)
            await asyncio.sleep(delay)
            delay = min(delay * 1.5, 30.0)


async def _task_consumer_loop(app: FastAPI) -> None:
    logger.info(
        "task consumer enabled base=%s poll=%.2fs",
        TASK_BASE_URL,
        TASK_POLL_SECS,
    )
    loop = asyncio.get_running_loop()

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
            text = await loop.run_in_executor(app.state.executor, ocr_image_bytes, data)
            await _task_complete_with_retry(app, int(task_id), ocr_text=text or "")
            app.state.consumer_state["stats"]["completed"] += 1
            app.state.metrics["imagesOk"] += 1
            logger.info("completed taskId=%s text_len=%s", task_id, len(text or ""))
        except asyncio.CancelledError:
            raise
        except Exception as e:
            app.state.consumer_state["stats"]["failed"] += 1
            app.state.metrics["imagesFail"] += 1
            logger.exception("task failed taskId=%s err=%s", task_id, e)
            await _task_complete_with_retry(app, int(task_id), fail_reason=str(e))


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
    app.state.task_http = httpx.AsyncClient(
        timeout=timeout,
        follow_redirects=True,
        headers={"User-Agent": "paddleocr-task-consumer/1.0"},
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

    app.state.started_at = datetime.now(timezone.utc)
    app.state.metrics = {
        "imagesTotal": 0,
        "imagesOk": 0,
        "imagesFail": 0,
    }
    app.state.consumer_state = {
        "enabled": TASK_CONSUMER_ENABLED,
        "paused": False,
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
    return """<!doctype html>
<html>
<head>
  <meta charset=\"utf-8\" />
  <meta name=\"viewport\" content=\"width=device-width, initial-scale=1\" />
  <title>OCR Worker</title>
  <style>
    :root {
      --bg0:#070A12;
      --bg1:#0B1120;
      --panel:rgba(15, 23, 42, .78);
      --panel2:rgba(2, 6, 23, .55);
      --stroke:rgba(148, 163, 184, .18);
      --text:#E7EEF9;
      --muted:#9FB3D7;
      --good:#2DD4BF;
      --warn:#FBBF24;
      --bad:#FB7185;
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
    }
    header {
      padding: 18px 18px 14px;
      border-bottom:1px solid var(--stroke);
      display:flex;
      align-items:flex-end;
      justify-content:space-between;
      gap:12px;
    }
    header h1 {
      margin:0;
      font: 700 18px/1.15 var(--serif);
      letter-spacing:.2px;
    }
    header .sub {
      color:var(--muted);
      font-size:12px;
      margin-top:6px;
    }
    .pill {
      font-size:12px;
      color:var(--muted);
      padding:6px 10px;
      border:1px solid var(--stroke);
      border-radius:999px;
      background:rgba(2,6,23,.35);
      backdrop-filter: blur(10px);
      white-space:nowrap;
    }
    main {
      display:grid;
      grid-template-columns: 1fr 1.4fr;
      gap: 14px;
      padding: 14px;
    }
    .card {
      border:1px solid var(--stroke);
      border-radius: 14px;
      background: var(--panel);
      box-shadow: 0 18px 60px rgba(0,0,0,.35);
      overflow:hidden;
    }
    .card .hd {
      padding: 12px 12px 10px;
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
    .card .bd { padding: 12px; }
    .grid { display:grid; grid-template-columns: 150px 1fr; gap:10px; }
    .row { padding: 8px 0; border-bottom:1px dashed rgba(148,163,184,.16); }
    .row:last-child { border-bottom:none; }
    .k { color: var(--muted); font-size: 12px; }
    .v { font-family: var(--mono); word-break: break-all; white-space: pre-wrap; }
    .btns { display:flex; gap:8px; flex-wrap:wrap; justify-content:flex-end; }
    button {
      appearance:none;
      border:1px solid var(--stroke);
      background: rgba(2,6,23,.55);
      color: var(--text);
      padding: 8px 10px;
      border-radius: 10px;
      font-weight: 600;
      cursor:pointer;
    }
    button:hover { border-color: rgba(148,163,184,.35); }
    button:active { transform: translateY(1px); }
    .good { color: var(--good); }
    .bad { color: var(--bad); }
    .warn { color: var(--warn); }
    pre {
      margin:0;
      padding: 12px;
      height: 70vh;
      overflow:auto;
      background: var(--panel2);
      border:1px solid rgba(148,163,184,.16);
      border-radius: 12px;
      font-family: var(--mono);
      font-size: 12px;
      line-height: 1.4;
      white-space: pre-wrap;
    }
    @media (max-width: 980px) { main { grid-template-columns: 1fr; } pre { height: 52vh; } }
  </style>
</head>
<body>
  <header>
    <div>
      <h1>OCR Worker</h1>
      <div class=\"sub\">Auto-claim tasks, run OCR, auto-complete + live logs</div>
    </div>
    <div class=\"pill\" id=\"status\">connecting...</div>
  </header>

  <main>
    <section class=\"card\">
      <div class=\"hd\">
        <div class=\"t\">Runtime</div>
        <div class=\"btns\">
          <button id=\"pauseBtn\">Pause</button>
          <button id=\"resumeBtn\">Resume</button>
          <a class=\"pill\" href=\"/docs\" target=\"_blank\" style=\"text-decoration:none\">OpenAPI</a>
        </div>
      </div>
      <div class=\"bd\">
        <div class=\"grid\">
          <div class=\"row\"><div class=\"k\">Uptime</div><div class=\"v\" id=\"uptime\">-</div></div>
          <div class=\"row\"><div class=\"k\">Images Total</div><div class=\"v\" id=\"imgTotal\">0</div></div>
          <div class=\"row\"><div class=\"k\">Images OK</div><div class=\"v good\" id=\"imgOk\">0</div></div>
          <div class=\"row\"><div class=\"k\">Images Fail</div><div class=\"v bad\" id=\"imgFail\">0</div></div>
          <div class=\"row\"><div class=\"k\">Consumer</div><div class=\"v\" id=\"consumer\">-</div></div>
          <div class=\"row\"><div class=\"k\">Current Task</div><div class=\"v\" id=\"task\">-</div></div>
          <div class=\"row\"><div class=\"k\">Backend</div><div class=\"v\" id=\"backend\">-</div></div>
        </div>
      </div>
    </section>

    <section class=\"card\">
      <div class=\"hd\"><div class=\"t\">Logs</div><div class=\"pill\">auto-scroll</div></div>
      <div class=\"bd\"><pre id=\"log\">Loading...</pre></div>
    </section>
  </main>

  <script>
    let last = 0;
    function fmtSec(s){
      const h = Math.floor(s/3600);
      const m = Math.floor((s%3600)/60);
      const ss = Math.floor(s%60);
      return `${h}h ${m}m ${ss}s`;
    }
    function fmtTask(t){
      if(!t) return '-';
      const head = `id=${t.id} spuId=${t.spuId ?? ''} type=${t.imageType ?? ''}`.trim();
      return head + "\n" + (t.imageUrl || '');
    }
    async function post(path){
      const r = await fetch(path, {method:'POST'});
      if(!r.ok) throw new Error('http ' + r.status);
    }
    document.getElementById('pauseBtn').onclick = () => post('/api/consumer/pause');
    document.getElementById('resumeBtn').onclick = () => post('/api/consumer/resume');

    async function tick(){
      try{
        const s = await fetch('/api/state').then(r=>r.json());
        document.getElementById('uptime').textContent = fmtSec(s.uptimeSeconds || 0);
        document.getElementById('imgTotal').textContent = s.metrics.imagesTotal;
        document.getElementById('imgOk').textContent = s.metrics.imagesOk;
        document.getElementById('imgFail').textContent = s.metrics.imagesFail;

        const c = s.consumer;
        const mode = c.enabled ? (c.paused ? 'paused' : (c.idle ? 'idle' : 'working')) : 'api-only';
        document.getElementById('consumer').textContent = mode;
        document.getElementById('task').textContent = fmtTask(c.currentTask);
        document.getElementById('backend').textContent = s.config.taskBaseUrl + ` (poll ${s.config.taskPollSecs}s)`;

        const status = document.getElementById('status');
        status.textContent = mode;
        status.className = 'pill ' + (mode==='working' ? 'good' : (mode==='paused' ? 'warn' : ''));

        const logs = await fetch('/api/logs?since=' + last).then(r=>r.json());
        if(logs.items && logs.items.length){
          const pre = document.getElementById('log');
          if(last === 0) pre.textContent = '';
          for(const it of logs.items){
            pre.textContent += `[${it.ts}] ${it.level} ${it.msg}\n`;
            last = it.n;
          }
          pre.scrollTop = pre.scrollHeight;
        }
      }catch(e){
        document.getElementById('status').textContent = 'error';
      }
    }
    setInterval(tick, 1000);
    tick();
  </script>
</body>
</html>"""


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
            text = await loop.run_in_executor(app.state.executor, ocr_image_bytes, data)
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
