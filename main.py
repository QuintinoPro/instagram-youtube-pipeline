"""
Pipeline Instagram → YouTube

Uso:
    python main.py              # Coleta + upload (modo padrão)
    python main.py --collect    # Só baixa vídeos do Instagram
    python main.py --upload     # Só faz upload dos vídeos pendentes
    python main.py --status     # Resumo rápido no terminal
    python main.py --report     # Relatório completo + exporta CSV
"""

import argparse
import sys
from loguru import logger

import config
from modules import instagram_collector, youtube_uploader, metadata_manager, reporter


def setup_logging() -> None:
    logger.remove()
    logger.add(
        sys.stderr,
        level="INFO",
        colorize=True,
        format="<green>{time:HH:mm:ss}</green> | <level>{level:<8}</level> | {message}",
    )
    logger.add(
        config.LOG_FILE,
        level="DEBUG",
        rotation="10 MB",
        retention="30 days",
        encoding="utf-8",
        format="{time:YYYY-MM-DD HH:mm:ss} | {level:<8} | {message}",
    )


def cmd_collect() -> None:
    logger.info("=== COLETA: Instagram ===")
    downloaded = instagram_collector.collect(max_videos=config.MAX_COLLECT_PER_RUN)
    logger.info(f"Coleta finalizada: {len(downloaded)} vídeo(s) novos baixados.")
    reporter.generate_csv()


def cmd_upload() -> None:
    logger.info("=== UPLOAD: YouTube ===")
    success, failed = youtube_uploader.upload_queue(max_uploads=config.MAX_UPLOADS_PER_RUN)
    logger.info(f"Upload finalizado: {success} agendado(s), {failed} erro(s).")
    reporter.generate_csv()


def cmd_status() -> None:
    records = metadata_manager.get_all()
    if not records:
        print("Nenhum vídeo registrado ainda.")
        return

    from collections import Counter
    counts = Counter(r["status"] for r in records)

    pending_upload = counts.get("downloaded", 0)
    scheduled = counts.get("scheduled", 0)
    uploaded = counts.get("uploaded", 0)
    errors = counts.get("error", 0)

    print(f"\n{'-'*50}")
    print(f"  Total registrado : {len(records)}")
    print(f"  Aguardando upload: {pending_upload}")
    print(f"  Agendados YouTube: {scheduled}")
    print(f"  Publicados       : {uploaded}")
    print(f"  Erros            : {errors}")
    print(f"{'-'*50}")

    sched_list = metadata_manager.get_scheduled()
    if sched_list:
        print("\n  Próximas publicações:")
        for r in sorted(sched_list, key=lambda x: x.get("youtube_publish_at", "")):
            pub = (r.get("youtube_publish_at") or "")[:16].replace("T", " ")
            print(f"    {pub} UTC | {r['shortcode']} | {r.get('youtube_video_id', '')}")
    print()


def cmd_report() -> None:
    reporter.generate_csv()
    reporter.print_report()


def cmd_diagnostico() -> None:
    """Exibe diagnóstico de inicialização e orienta o próximo passo."""
    from datetime import datetime, timezone
    from collections import Counter

    records = metadata_manager.get_all()
    counts = Counter(r["status"] for r in records)

    pending_download = counts.get("downloaded", 0)
    scheduled = counts.get("scheduled", 0)
    uploaded = counts.get("uploaded", 0)

    # Verifica vídeos agendados que já deveriam ter sido publicados
    now = datetime.now(timezone.utc)
    recem_publicados = []
    for r in records:
        if r["status"] == "scheduled" and r.get("youtube_publish_at"):
            pub = datetime.fromisoformat(r["youtube_publish_at"].replace("Z", "+00:00"))
            if pub <= now:
                recem_publicados.append(r)

    print(f"\n{'═'*60}")
    print(f"  DIAGNÓSTICO — {now.strftime('%Y-%m-%d %H:%M')} UTC")
    print(f"{'═'*60}")
    print(f"  Vídeos no banco        : {len(records)}")
    print(f"  Publicados no YouTube  : {uploaded}")
    print(f"  Agendados (futuros)    : {scheduled - len(recem_publicados)}")
    print(f"  Aguardando upload      : {pending_download}")

    if recem_publicados:
        print(f"\n  ⚡ {len(recem_publicados)} vídeo(s) já passaram da data de publicação.")
        print(f"     Execute: python main.py --sync-status")

    print(f"\n{'─'*60}")
    print(f"  O QUE FAZER AGORA:")
    print(f"{'─'*60}")

    if pending_download > 0:
        print(f"  ✅ Você tem {pending_download} vídeo(s) prontos para subir ao YouTube.")
        print(f"     → python main.py --upload")
    else:
        print(f"  ℹ  Nenhum vídeo aguardando upload.")

    print(f"\n  🔄 Para baixar novos vídeos do Instagram:")
    print(f"     → python main.py --collect")

    next_scheduled = sorted(
        [r for r in records if r["status"] == "scheduled" and r.get("youtube_publish_at")],
        key=lambda x: x["youtube_publish_at"]
    )
    if next_scheduled:
        prox = next_scheduled[0]
        pub = prox["youtube_publish_at"][:16].replace("T", " ")
        print(f"\n  📅 Próxima publicação agendada: {pub} UTC")

    print(f"\n  📊 Relatório completo: python main.py --report")
    print(f"{'═'*60}\n")


def _sync_published_status() -> None:
    synced = metadata_manager.sync_published()
    if synced:
        reporter.generate_csv()
        logger.info(f"{synced} vídeo(s) marcados como publicados.")
    else:
        logger.info("Nenhum vídeo novo para marcar como publicado.")


def main() -> None:
    setup_logging()

    parser = argparse.ArgumentParser(description="Pipeline Instagram → YouTube")
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--collect", action="store_true", help="Baixa vídeos do Instagram")
    group.add_argument("--upload", action="store_true", help="Faz upload dos vídeos pendentes")
    group.add_argument("--status", action="store_true", help="Resumo rápido")
    group.add_argument("--report", action="store_true", help="Relatório completo + exporta CSV")
    group.add_argument("--sync-status", action="store_true", help="Marca como publicados os vídeos cuja data já passou")
    group.add_argument("--retry-errors", action="store_true", help="Recoloca vídeos com erro na fila e tenta upload novamente")
    args = parser.parse_args()

    if args.collect:
        cmd_collect()
        cmd_status()
    elif args.upload:
        cmd_upload()
        cmd_status()
    elif args.status:
        cmd_status()
    elif args.report:
        cmd_report()
    elif args.sync_status:
        _sync_published_status()
        cmd_status()
    elif args.retry_errors:
        count = metadata_manager.reset_errors()
        if count:
            logger.info(f"{count} vídeo(s) prontos para nova tentativa.")
            cmd_upload()
        else:
            logger.info("Nenhum vídeo com erro encontrado.")
        cmd_status()
    else:
        # Sem argumentos: mostra diagnóstico
        cmd_diagnostico()


if __name__ == "__main__":
    main()
