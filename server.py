"""
Dashboard web do Pipeline BlockchainRio.

Inicie com:
    python server.py

Acesse em: http://localhost:8000
"""

import asyncio
import json
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from loguru import logger
from pydantic import BaseModel

import config
from modules import instagram_collector, metadata_manager, reporter, youtube_uploader

# ─── SSE state ────────────────────────────────────────────────────────────────
_sse_clients: list[asyncio.Queue] = []
_loop: asyncio.AbstractEventLoop | None = None


def _fan_out(payload: str) -> None:
    """Chamado no event loop via call_soon_threadsafe. Entrega para cada cliente SSE."""
    for q in list(_sse_clients):
        try:
            q.put_nowait(payload)
        except asyncio.QueueFull:
            pass


def _loguru_sink(message) -> None:
    """Sink do loguru que roda na worker thread e entrega ao event loop."""
    if _loop is None or not _loop.is_running():
        return
    record = message.record
    payload = json.dumps({
        "level": record["level"].name.lower(),
        "message": record["message"],
        "time": record["time"].strftime("%H:%M:%S"),
    })
    _loop.call_soon_threadsafe(_fan_out, payload)


# ─── Task state ───────────────────────────────────────────────────────────────
_is_running = False
_current_task = ""
_executor = ThreadPoolExecutor(max_workers=1)


def _run_task(task_name: str, fn, *args) -> bool:
    """Envia função síncrona para executor. Retorna False se já há tarefa rodando."""
    global _is_running, _current_task

    if _is_running:
        return False

    _is_running = True
    _current_task = task_name

    def wrapper():
        global _is_running, _current_task
        try:
            fn(*args)
            reporter.generate_csv()
        except Exception as e:
            logger.error(f"Erro na tarefa '{task_name}': {e}")
        finally:
            _is_running = False
            _current_task = ""

    _executor.submit(wrapper)
    return True


def _sync_status() -> None:
    synced = metadata_manager.sync_published()
    if synced:
        logger.info(f"{synced} vídeo(s) atualizados para 'publicado'.")
    else:
        logger.info("Nenhum vídeo novo para marcar como publicado.")


# ─── App ──────────────────────────────────────────────────────────────────────
app = FastAPI(title=config.PIPELINE_TITLE)

Path("static").mkdir(exist_ok=True)
app.mount("/static", StaticFiles(directory="static"), name="static")


@app.on_event("startup")
async def startup():
    global _loop
    _loop = asyncio.get_event_loop()
    logger.add(
        _loguru_sink,
        level="INFO",
        format="{message}",
        colorize=False,
    )
    logger.info("Dashboard iniciado em http://localhost:8000")


# ─── Routes ───────────────────────────────────────────────────────────────────
@app.get("/")
async def index():
    return FileResponse("static/index.html")


@app.get("/api/status")
def get_status():
    records = metadata_manager.get_all()
    counts = Counter(r["status"] for r in records)

    scheduled = metadata_manager.get_scheduled()
    now_utc = datetime.now(timezone.utc)
    future = [
        r["youtube_publish_at"] for r in scheduled
        if r.get("youtube_publish_at") and
        datetime.fromisoformat(r["youtube_publish_at"].replace("Z", "+00:00")) > now_utc
    ]
    next_pub = min(future) if future else None

    return {
        "total": len(records),
        "downloaded": counts.get("downloaded", 0),
        "scheduled": counts.get("scheduled", 0),
        "uploaded": counts.get("uploaded", 0),
        "error": counts.get("error", 0),
        "is_running": _is_running,
        "current_task": _current_task,
        "next_publish": next_pub,
        "max_uploads": config.MAX_UPLOADS_PER_RUN,
    }


@app.get("/api/config")
def get_config():
    # Relê settings.json em tempo real para refletir mudanças imediatas
    s = config._load_settings()
    def v(key, fallback):
        return s.get(key) or fallback
    return {
        "brand_name":        v("PIPELINE_BRAND_NAME", config.PIPELINE_BRAND_NAME),
        "brand_logo":        v("PIPELINE_BRAND_LOGO", config.PIPELINE_BRAND_LOGO),
        "title":             v("PIPELINE_TITLE",      config.PIPELINE_TITLE),
        "instagram_profile": v("TARGET_INSTAGRAM_PROFILE", config.TARGET_INSTAGRAM_PROFILE),
    }


class SettingsPayload(BaseModel):
    PIPELINE_BRAND_NAME:      str = ""
    PIPELINE_BRAND_LOGO:      str = ""
    PIPELINE_TITLE:           str = ""
    TARGET_INSTAGRAM_PROFILE: str = ""
    INSTAGRAM_SESSION_ID:     str = ""
    DAILY_SLOTS:              list[list[int]] = [[13, 0]]
    MAX_COLLECT_PER_RUN:      int = 30
    MAX_UPLOADS_PER_RUN:      int = 6


@app.get("/api/settings")
def get_settings():
    """Retorna configurações atuais (settings.json > .env > defaults)."""
    import os
    s = config._load_settings()
    def v(key, default):
        return s.get(key) or os.getenv(key, default)

    # Monta DAILY_SLOTS: usa o novo campo ou faz backward compat com UPLOAD_HOUR/MINUTE
    daily_slots = s.get("DAILY_SLOTS")
    if not daily_slots:
        hour   = int(s.get("UPLOAD_HOUR")   or os.getenv("UPLOAD_HOUR",   "13"))
        minute = int(s.get("UPLOAD_MINUTE") or os.getenv("UPLOAD_MINUTE", "0"))
        daily_slots = [[hour, minute]]

    return {
        "PIPELINE_BRAND_NAME":      v("PIPELINE_BRAND_NAME",      "My Pipeline"),
        "PIPELINE_BRAND_LOGO":      v("PIPELINE_BRAND_LOGO",      "YT"),
        "PIPELINE_TITLE":           v("PIPELINE_TITLE",           "Instagram → YouTube Pipeline"),
        "TARGET_INSTAGRAM_PROFILE": v("TARGET_INSTAGRAM_PROFILE", ""),
        "INSTAGRAM_SESSION_ID":     v("INSTAGRAM_SESSION_ID",     ""),
        "DAILY_SLOTS":              daily_slots,
        "MAX_COLLECT_PER_RUN":      int(v("MAX_COLLECT_PER_RUN",  "30")),
        "MAX_UPLOADS_PER_RUN":      int(v("MAX_UPLOADS_PER_RUN",  "6")),
    }


@app.post("/api/settings")
def save_settings(payload: SettingsPayload):
    """Persiste configurações em settings.json."""
    data = payload.model_dump()
    try:
        config.SETTINGS_FILE.write_text(
            json.dumps(data, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    logger.info("Configurações salvas via dashboard.")
    return {"ok": True}


@app.get("/api/videos")
def get_videos():
    records = metadata_manager.get_all()
    result = []
    for r in sorted(records, key=lambda x: x.get("instagram_date", ""), reverse=True):
        item = {
            "shortcode": r.get("shortcode", ""),
            "instagram_date": (r.get("instagram_date") or "")[:10],
            "status": r.get("status", ""),
            "youtube_video_id": r.get("youtube_video_id"),
            "youtube_url": f"https://youtube.com/watch?v={r['youtube_video_id']}" if r.get("youtube_video_id") else None,
            "youtube_publish_at": (r.get("youtube_publish_at") or "")[:16].replace("T", " "),
            "uploaded_at": (r.get("uploaded_at") or "")[:10],
            "caption": (r.get("caption") or "").replace("\n", " ")[:80],
        }
        result.append(item)
    return result


@app.post("/api/collect", status_code=202)
def start_collect():
    started = _run_task(
        "collect",
        instagram_collector.collect,
        config.MAX_COLLECT_PER_RUN,
    )
    if not started:
        return {"error": "Já há uma tarefa em execução.", "current": _current_task}
    return {"started": True, "task": "collect"}


@app.post("/api/upload", status_code=202)
def start_upload():
    started = _run_task(
        "upload",
        youtube_uploader.upload_queue,
        config.MAX_UPLOADS_PER_RUN,
    )
    if not started:
        return {"error": "Já há uma tarefa em execução.", "current": _current_task}
    return {"started": True, "task": "upload"}


@app.post("/api/sync", status_code=202)
def start_sync():
    started = _run_task("sync", _sync_status)
    if not started:
        return {"error": "Já há uma tarefa em execução.", "current": _current_task}
    return {"started": True, "task": "sync"}


def _retry_errors() -> None:
    count = metadata_manager.reset_errors()
    if count:
        logger.info(f"{count} vídeo(s) recolocados na fila — iniciando upload...")
        youtube_uploader.upload_queue(config.MAX_UPLOADS_PER_RUN)
    else:
        logger.info("Nenhum vídeo com erro encontrado.")


@app.post("/api/retry-errors", status_code=202)
def start_retry_errors():
    started = _run_task("retry-errors", _retry_errors)
    if not started:
        return {"error": "Já há uma tarefa em execução.", "current": _current_task}
    return {"started": True, "task": "retry-errors"}


@app.get("/api/logs/stream")
async def sse_logs():
    client_q: asyncio.Queue = asyncio.Queue(maxsize=500)
    _sse_clients.append(client_q)

    async def generate():
        try:
            # Mensagem inicial de conexão
            yield f"data: {json.dumps({'level': 'info', 'time': datetime.now().strftime('%H:%M:%S'), 'message': 'Dashboard conectado — aguardando ações.'})}\n\n"
            while True:
                try:
                    msg = await asyncio.wait_for(client_q.get(), timeout=20.0)
                    yield f"data: {msg}\n\n"
                except asyncio.TimeoutError:
                    yield ": heartbeat\n\n"
        finally:
            if client_q in _sse_clients:
                _sse_clients.remove(client_q)

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("server:app", host="127.0.0.1", port=8000, reload=False)
