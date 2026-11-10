import logging
import sys
import threading
from datetime import datetime
from pathlib import Path
from typing import Any

LOGS_DIR = Path(__file__).resolve().parent.parent.parent / "logs"

LOG_FORMAT = "%(asctime)s | %(levelname)-8s | %(name)s | %(message)s"
DATE_FORMAT = "%Y-%m-%d %H:%M:%S"


class DailyTxtFileHandler(logging.Handler):
    """Logging handler that automatically directs logs to date-stamped .txt files (app_YYYY-MM-DD.txt)."""

    def __init__(self, logs_dir: Path, encoding: str = "utf-8"):
        super().__init__()
        self.logs_dir = logs_dir
        self.logs_dir.mkdir(parents=True, exist_ok=True)
        self.encoding = encoding
        self._current_date: str | None = None
        self._stream: Any = None
        self._lock = threading.Lock()
        self._ensure_stream()

    def _ensure_stream(self) -> None:
        if getattr(sys, "meta_path", None) is None:
            return
        try:
            today = datetime.now().strftime("%Y-%m-%d")
        except Exception:
            return

        if today != self._current_date or self._stream is None:
            if self._stream is not None:
                try:
                    self._stream.close()
                except Exception:
                    pass
            self._current_date = today
            file_path = self.logs_dir / f"app_{today}.txt"
            try:
                self._stream = open(file_path, "a", encoding=self.encoding, buffering=1)
            except Exception:
                self._stream = None

    def emit(self, record: logging.LogRecord) -> None:
        if getattr(sys, "meta_path", None) is None:
            return
        with self._lock:
            try:
                self._ensure_stream()
                if self._stream and not self._stream.closed:
                    msg = self.format(record)
                    self._stream.write(msg + "\n")
            except (ImportError, OSError):
                pass
            except Exception:
                try:
                    self.handleError(record)
                except Exception:
                    pass

    def close(self) -> None:
        with self._lock:
            if self._stream is not None:
                try:
                    self._stream.close()
                except Exception:
                    pass
                self._stream = None
            super().close()


_logging_initialized = False


def setup_app_logging(log_level: int = logging.INFO) -> None:
    """Configure unified logging with console stream and daily rotating .txt file handlers."""
    global _logging_initialized
    if _logging_initialized:
        return

    root_logger = logging.getLogger()
    root_logger.setLevel(log_level)

    formatter = logging.Formatter(fmt=LOG_FORMAT, datefmt=DATE_FORMAT)

    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(log_level)
    console_handler.setFormatter(formatter)
    root_logger.addHandler(console_handler)

    file_handler = DailyTxtFileHandler(LOGS_DIR)
    file_handler.setLevel(log_level)
    file_handler.setFormatter(formatter)
    root_logger.addHandler(file_handler)

    logging.getLogger("aiosqlite").setLevel(logging.WARNING)
    logging.getLogger("urllib3").setLevel(logging.WARNING)
    logging.getLogger("asyncio").setLevel(logging.WARNING)

    _logging_initialized = True


def list_available_log_dates() -> list[str]:
    """Return sorted list of dates (YYYY-MM-DD) that have log files available."""
    if not LOGS_DIR.is_dir():
        return [datetime.now().strftime("%Y-%m-%d")]

    dates: list[str] = []
    for file in LOGS_DIR.glob("app_*.txt"):
        stem = file.stem  # e.g. "app_2026-10-29"
        if stem.startswith("app_"):
            date_part = stem[4:]
            dates.append(date_part)

    today = datetime.now().strftime("%Y-%m-%d")
    if today not in dates:
        dates.append(today)

    dates.sort(reverse=True)
    return dates


def get_log_file_path(date_str: str | None = None) -> Path:
    """Return path to log file for specified date or today."""
    if not date_str:
        date_str = datetime.now().strftime("%Y-%m-%d")
    clean_date = "".join(c for c in date_str if c.isalnum() or c == "-")
    return LOGS_DIR / f"app_{clean_date}.txt"


def classify_log_source(logger_name: str) -> str:
    """Classify log record origin into 'web' or 'bot'."""
    lower = logger_name.lower().strip()
    if lower.startswith(("src.web", "uvicorn", "fastapi")):
        return "web"
    return "bot"


def count_log_sources(date_str: str | None = None) -> dict[str, int]:
    """Count total, bot, and web log entries for the given date."""
    path = get_log_file_path(date_str)
    counts = {"all": 0, "bot": 0, "web": 0}
    if not path.is_file():
        return counts

    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            for line in f:
                line_str = line.strip()
                if not line_str:
                    continue
                counts["all"] += 1
                parts = line_str.split(" | ", 3)
                name = parts[2] if len(parts) >= 3 else ""
                src = classify_log_source(name)
                counts[src] += 1
    except Exception:
        pass
    return counts


def read_parsed_logs(
    date_str: str | None = None,
    level: str | None = None,
    search: str | None = None,
    source: str = "all",
    limit: int = 500,
) -> list[dict[str, str]]:
    """Read and filter log lines from the specified date's .txt file."""
    path = get_log_file_path(date_str)
    if not path.is_file():
        return []

    results: list[dict[str, str]] = []
    target_level = level.upper().strip() if level and level.upper() != "ALL" else None
    search_term = search.lower().strip() if search else None
    target_source = source.lower().strip() if source and source.lower() != "all" else None

    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            lines = f.readlines()

        for line in lines:
            line_str = line.rstrip("\r\n")
            if not line_str:
                continue

            parts = line_str.split(" | ", 3)
            if len(parts) == 4:
                ts, lvl, name, msg = parts
                lvl_clean = lvl.strip()
            else:
                ts = ""
                lvl_clean = "INFO"
                name = ""
                msg = line_str

            item_source = classify_log_source(name)

            if target_source and item_source != target_source:
                continue

            if target_level and lvl_clean != target_level:
                continue

            if search_term and search_term not in line_str.lower():
                continue

            results.append(
                {
                    "timestamp": ts,
                    "level": lvl_clean,
                    "logger": name,
                    "message": msg,
                    "source": item_source,
                    "raw": line_str,
                }
            )
    except Exception as exc:
        logging.getLogger(__name__).error("Failed to read log file %s: %s", path, exc)

    if limit and len(results) > limit:
        return results[-limit:]
    return results
