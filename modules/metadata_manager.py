"""
Gerencia o arquivo JSON de rastreamento de vídeos.

Schema de cada entrada:
{
    "shortcode": str,           # ID único do post no Instagram
    "filename": str,            # Caminho relativo do arquivo de vídeo
    "caption": str,             # Legenda original do Instagram
    "instagram_date": str,      # ISO 8601
    "status": str,              # downloaded | scheduled | uploaded | error
    "youtube_video_id": str | null,
    "youtube_publish_at": str | null,  # ISO 8601 UTC
    "uploaded_at": str | null          # ISO 8601
}
"""

import json
from datetime import datetime, timezone
from pathlib import Path
from loguru import logger

import config


def _load() -> list[dict]:
    if not config.METADATA_FILE.exists():
        return []
    with open(config.METADATA_FILE, encoding="utf-8") as f:
        return json.load(f)


def _save(records: list[dict]) -> None:
    with open(config.METADATA_FILE, "w", encoding="utf-8") as f:
        json.dump(records, f, ensure_ascii=False, indent=2)


def get_all() -> list[dict]:
    return _load()


def get_by_shortcode(shortcode: str) -> dict | None:
    for record in _load():
        if record["shortcode"] == shortcode:
            return record
    return None


def exists(shortcode: str) -> bool:
    return get_by_shortcode(shortcode) is not None


def add(record: dict) -> None:
    """Adiciona novo registro. Ignora silenciosamente se já existir."""
    if exists(record["shortcode"]):
        logger.debug(f"Shortcode {record['shortcode']} já registrado — ignorando.")
        return
    records = _load()
    records.append(record)
    _save(records)
    logger.info(f"Registrado: {record['shortcode']} ({record['status']})")


def update_status(
    shortcode: str,
    status: str,
    youtube_video_id: str | None = None,
    youtube_publish_at: str | None = None,
) -> None:
    records = _load()
    for record in records:
        if record["shortcode"] == shortcode:
            record["status"] = status
            if youtube_video_id is not None:
                record["youtube_video_id"] = youtube_video_id
            if youtube_publish_at is not None:
                record["youtube_publish_at"] = youtube_publish_at
            if status == "uploaded":
                record["uploaded_at"] = datetime.now(timezone.utc).isoformat()
            _save(records)
            logger.info(f"Status atualizado: {shortcode} → {status}")
            return
    logger.warning(f"Shortcode não encontrado para update: {shortcode}")


def get_pending_upload() -> list[dict]:
    """Retorna vídeos com status 'downloaded' (prontos para upload)."""
    return [r for r in _load() if r["status"] == "downloaded"]


def reset_errors() -> int:
    """Recoloca vídeos com status 'error' de volta para 'downloaded'. Retorna quantidade resetada."""
    records = _load()
    count = 0
    for record in records:
        if record["status"] == "error":
            record["status"] = "downloaded"
            record["youtube_video_id"] = None
            record["youtube_publish_at"] = None
            count += 1
    if count:
        _save(records)
        logger.info(f"{count} vídeo(s) com erro recolocados na fila.")
    return count


def get_scheduled() -> list[dict]:
    """Retorna vídeos já agendados no YouTube."""
    return [r for r in _load() if r["status"] == "scheduled"]


def sync_published() -> int:
    """Marca como 'uploaded' os vídeos cujo publishAt já passou. Retorna quantidade atualizada."""
    now = datetime.now(timezone.utc)
    synced = 0
    for r in _load():
        if r["status"] == "scheduled" and r.get("youtube_publish_at"):
            pub = datetime.fromisoformat(r["youtube_publish_at"].replace("Z", "+00:00"))
            if pub <= now:
                update_status(r["shortcode"], "uploaded")
                logger.success(f"Marcado como publicado: {r['shortcode']} (publicado em {pub.date()})")
                synced += 1
    return synced


def next_publish_slot(scheduled: list[dict]) -> datetime:
    """
    Calcula o próximo slot de publicação disponível.
    Suporta N vídeos/dia conforme DAILY_SLOTS em config (UTC).
    """
    from datetime import timedelta, date as date_type

    slots = config.get_daily_slots()  # [(hora, minuto), ...]
    now = datetime.now(timezone.utc)

    # Ocupados: conjunto de (date, hour) já agendados
    occupied: set[tuple[date_type, int]] = set()
    for r in scheduled:
        if r.get("youtube_publish_at"):
            dt = datetime.fromisoformat(r["youtube_publish_at"].replace("Z", "+00:00"))
            occupied.add((dt.date(), dt.hour))

    candidate_date = now.date()
    for _ in range(365):
        for hour, minute in slots:
            candidate = datetime(
                candidate_date.year, candidate_date.month, candidate_date.day,
                hour, minute, 0, tzinfo=timezone.utc,
            )
            if candidate > now and (candidate_date, hour) not in occupied:
                return candidate
        candidate_date += timedelta(days=1)

    return now + timedelta(days=7)
