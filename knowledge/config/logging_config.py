import logging
from logging.handlers import RotatingFileHandler

from knowledge.config.settings import Settings


LOG_FORMAT = "%(asctime)s | %(levelname)s | %(name)s | %(message)s"
LOG_DATE_FORMAT = "%Y-%m-%d %H:%M:%S"
THIRD_PARTY_LOGGERS = ("httpx", "httpcore", "openai", "chromadb", "jieba")
HANDLER_MARKER = "_knowledge_rag_handler"


def configure_logging(settings: Settings) -> None:
    """Configure idempotent console and rotating-file application logging."""

    log_file = settings.resolved_log_file
    log_file.parent.mkdir(parents=True, exist_ok=True)

    root_logger = logging.getLogger()
    for handler in list(root_logger.handlers):
        if getattr(handler, HANDLER_MARKER, False):
            root_logger.removeHandler(handler)
            handler.close()

    formatter = logging.Formatter(LOG_FORMAT, datefmt=LOG_DATE_FORMAT)
    level = getattr(logging, settings.log_level)

    console_handler = logging.StreamHandler()
    console_handler.setLevel(level)
    console_handler.setFormatter(formatter)
    setattr(console_handler, HANDLER_MARKER, True)

    file_handler = RotatingFileHandler(
        filename=log_file,
        maxBytes=settings.log_max_bytes,
        backupCount=settings.log_backup_count,
        encoding="utf-8",
    )
    file_handler.setLevel(level)
    file_handler.setFormatter(formatter)
    setattr(file_handler, HANDLER_MARKER, True)

    root_logger.setLevel(level)
    root_logger.addHandler(console_handler)
    root_logger.addHandler(file_handler)

    for logger_name in THIRD_PARTY_LOGGERS:
        logging.getLogger(logger_name).setLevel(logging.WARNING)

    logging.getLogger(__name__).info(
        "Logging configured level=%s file=%s max_bytes=%d backup_count=%d",
        settings.log_level,
        log_file,
        settings.log_max_bytes,
        settings.log_backup_count,
    )
