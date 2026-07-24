import logging
from pathlib import Path

import pytest

from knowledge.config.logging_config import configure_logging
from knowledge.config.settings import Settings


@pytest.fixture(autouse=True)
def restore_logging_state():
    root = logging.getLogger()
    original_handlers = list(root.handlers)
    original_level = root.level
    third_party_names = ("httpx", "httpcore", "openai", "chromadb", "jieba")
    third_party_levels = {
        name: logging.getLogger(name).level for name in third_party_names
    }
    yield
    for handler in list(root.handlers):
        if getattr(handler, "_knowledge_rag_handler", False):
            root.removeHandler(handler)
            handler.close()
    root.handlers[:] = original_handlers
    root.setLevel(original_level)
    for name, level in third_party_levels.items():
        logging.getLogger(name).setLevel(level)


def _settings(log_file: Path, **overrides) -> Settings:
    values = {
        "_env_file": None,
        "LOG_FILE": log_file,
        "LOG_LEVEL": "INFO",
        "LOG_MAX_BYTES": 10 * 1024 * 1024,
        "LOG_BACKUP_COUNT": 5,
    }
    values.update(overrides)
    return Settings(**values)


def _flush_handlers() -> None:
    for handler in logging.getLogger().handlers:
        handler.flush()


def test_configure_logging_writes_utf8_to_console_and_file(tmp_path, capsys):
    log_file = tmp_path / "logs" / "knowledge-rag.log"
    configure_logging(_settings(log_file))

    logging.getLogger("knowledge.test").info("中文日志已启用")
    _flush_handlers()

    assert "中文日志已启用" in capsys.readouterr().err
    content = log_file.read_text(encoding="utf-8")
    assert "INFO | knowledge.test | 中文日志已启用" in content


def test_configure_logging_is_idempotent(tmp_path, capsys):
    log_file = tmp_path / "knowledge-rag.log"
    settings = _settings(log_file)

    configure_logging(settings)
    configure_logging(settings)
    logging.getLogger("knowledge.test").warning("only-once-marker")
    _flush_handlers()

    assert capsys.readouterr().err.count("only-once-marker") == 1
    assert log_file.read_text(encoding="utf-8").count("only-once-marker") == 1


def test_configure_logging_honors_level_and_quiets_third_party_loggers(
    tmp_path,
    capsys,
):
    log_file = tmp_path / "knowledge-rag.log"
    configure_logging(_settings(log_file, LOG_LEVEL="WARNING"))

    logger = logging.getLogger("knowledge.test")
    logger.info("hidden-info")
    logger.warning("visible-warning")
    _flush_handlers()

    output = capsys.readouterr().err
    content = log_file.read_text(encoding="utf-8")
    assert "hidden-info" not in output + content
    assert "visible-warning" in output
    assert logging.getLogger("httpx").level == logging.WARNING
    assert logging.getLogger("jieba").level == logging.WARNING


def test_configure_logging_rotates_file_by_size(tmp_path):
    log_file = tmp_path / "knowledge-rag.log"
    configure_logging(
        _settings(
            log_file,
            LOG_MAX_BYTES=256,
            LOG_BACKUP_COUNT=2,
        )
    )

    logger = logging.getLogger("knowledge.rotation")
    for index in range(8):
        logger.warning("rotation-%s-%s", index, "x" * 160)
    _flush_handlers()

    assert log_file.exists()
    assert Path(f"{log_file}.1").exists()
    assert not Path(f"{log_file}.3").exists()
