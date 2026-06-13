#!/usr/bin/env python3
"""Cross-platform stock photo keywording tool.

Run with no arguments to open the desktop app:
    python stock_keyworder.py

Run from the command line:
    python stock_keyworder.py --folder ./photos --provider openai --api-key-file ./openai_key.txt
"""

from __future__ import annotations

import argparse
import base64
import csv
import datetime as dt
import hashlib
import html
import http.server
import json
import mimetypes
import os
import queue
import re
import shutil
import socket
import subprocess
import sys
import threading
import time
import traceback
import urllib.error
import urllib.parse
import urllib.request
import webbrowser
from dataclasses import asdict, dataclass
from io import BytesIO
from pathlib import Path
from typing import Any, Callable, Optional

try:
    from PIL import Image, ImageOps

    PIL_AVAILABLE = True
except Exception:
    Image = None
    ImageOps = None
    PIL_AVAILABLE = False


APP_NAME = "Stock Keyworder"
MAX_IMAGES = 500
IMAGE_EXTENSIONS = {
    ".jpg",
    ".jpeg",
    ".png",
    ".webp",
    ".bmp",
    ".tif",
    ".tiff",
}
PROVIDER_DEFAULT_MODELS = {
    "openai": "gpt-5.5",
    "gemini": "gemini-3.1-flash-lite",
}
PROVIDER_MODEL_SUGGESTIONS = {
    "openai": ["gpt-5.5", "gpt-4o-mini", "gpt-4o"],
    "gemini": [
        "gemini-3.1-flash-lite",
        "gemini-3.5-flash",
        "gemini-3.1-pro-preview",
        "gemini-3-flash-preview",
        "gemini-2.5-flash",
        "gemini-2.5-flash-lite",
        "gemini-2.5-pro",
    ],
}
MODEL_ALIASES = {
    "gpt-4o-mini": "gpt-4o-mini",
    "gpt4o-mini": "gpt-4o-mini",
    "gpt-4omini": "gpt-4o-mini",
    "gpt4omini": "gpt-4o-mini",
    "gpt-4o mini": "gpt-4o-mini",
    "gpt 4o mini": "gpt-4o-mini",
    "4o-mini": "gpt-4o-mini",
    "3.1flash-light": "gemini-3.1-flash-lite",
    "3.1flashlight": "gemini-3.1-flash-lite",
    "3.1 flash-light": "gemini-3.1-flash-lite",
    "3.1 flash light": "gemini-3.1-flash-lite",
    "3.1-flash-light": "gemini-3.1-flash-lite",
    "gemini 3.1 flash light": "gemini-3.1-flash-lite",
    "gemini-3.1-flash-light": "gemini-3.1-flash-lite",
    "3.1flash-lite": "gemini-3.1-flash-lite",
    "3.1flashlite": "gemini-3.1-flash-lite",
    "3.1 flash-lite": "gemini-3.1-flash-lite",
    "3.1 flash lite": "gemini-3.1-flash-lite",
    "3.1-flash-lite": "gemini-3.1-flash-lite",
    "gemini 3.1 flash lite": "gemini-3.1-flash-lite",
    "gemini-3.1-flash-lite": "gemini-3.1-flash-lite",
    "3.1pro": "gemini-3.1-pro-preview",
    "3.1 pro": "gemini-3.1-pro-preview",
    "gemini 3.1 pro": "gemini-3.1-pro-preview",
    "gemini-3.1-pro": "gemini-3.1-pro-preview",
    "gemini-3.1-pro-preview": "gemini-3.1-pro-preview",
    "3flash": "gemini-3-flash-preview",
    "3 flash": "gemini-3-flash-preview",
    "gemini 3 flash": "gemini-3-flash-preview",
    "gemini-3-flash": "gemini-3-flash-preview",
    "gemini-3-flash-preview": "gemini-3-flash-preview",
}
CONFIG_PATH = Path.home() / ".stock_keyworder_config.json"
USAGE_PATH = Path.home() / ".stock_keyworder_usage.json"
PROMPT_DIR = Path.home() / ".stock_keyworder_prompts"
KEY_CACHE_PATH = Path.home() / ".stock_keyworder_keys.json"
KEYCHAIN_SERVICE = "Stock Keyworder"
PENDING_PATH = Path.home() / ".stock_keyworder_pending.json"
DEFAULT_API_RETRY_BUFFER = 100
DEFAULT_DAILY_API_LIMIT = MAX_IMAGES + DEFAULT_API_RETRY_BUFFER
MAX_DAILY_API_LIMIT = 10000
CONFIRM_API_CALLS_THRESHOLD = 25
MAX_RETRY_COUNT = 3
DEFAULT_RETRY_COUNT = 2
CAPACITY_STOP_STREAK = 3
CAPACITY_RETRY_DELAYS = [20, 45, 90]
DEFAULT_MAX_FILE_MB = 64
MAX_FILE_MB = 512
MODEL_MAX_OUTPUT_TOKENS = 5000
DEFAULT_REUSE_SIMILAR_IMAGES = True
DEFAULT_SIMILARITY_THRESHOLD = 7
METADATA_SCHEMA_VERSION = 2

DEFAULT_PROMPT = """請為國際圖庫上架產生英文 metadata。

需求：
- Title 使用自然英文，最多 80 個字元，不要堆疊關鍵字。
- Description 使用 1 句自然英文，描述照片主要內容、構圖、用途。
- Keywords 輸出 35 到 49 個英文關鍵字，最重要、最可能被買家搜尋的 10 個放最前面。
- 避免臆測不可確認的品牌、地點、名人、族群、職業或事件。
- 若畫面有人臉、可識別人物、商標、車牌、受保護藝術品，請在 notes 標示可能需要 release 或有退件風險。
- 不要輸出 hashtag，不要重複關鍵字，不要加入不存在的物件。
"""

KEYWORD_OPTIMIZATION_GUIDE = """Keyword 欄位規則：
- 使用者需求中的圖庫規則、語言、數量、分隔方式與禁止詞優先於一般規則。
- keywords 必須使用使用者指定語言；例如要求日文圖庫時使用日文/片假名常用搜尋詞，要求英文圖庫時使用英文，不要自行混用語言。
- 若使用者在同一個 prompt 指定多個圖庫、語言或 keyword 數量，必須在 keyword_groups 逐組輸出，每組 name 使用圖庫/規則名稱，每組 keywords 的語言與數量都要符合該組規則。
- top-level keywords 必須等於第一組或主要圖庫的 keywords，供舊版流程相容；所有額外圖庫關鍵字放在 keyword_groups。
- 若使用者指定圖庫平台，請依該平台常見 metadata 習慣排列；若未指定平台，使用通用圖庫排序。
- 排序要以搜尋成交可能性為優先：主體/物件、動作、場景、構圖、可確認地點或文化元素、情緒/概念、用途、同義詞與長尾搜尋詞。
- 前 10 個 keywords 放最核心、最容易被搜尋且最能代表照片的詞；後面再補場景、風格、用途、抽象概念與同義詞。
- 不要堆入低價值泛詞，不要重複、不要 hashtag、不要加入看不見或不能確認的品牌/人物/事件。
- 若使用者要求特定 keyword 數量範圍，必須落在該範圍內；若沒有指定，輸出 35 到 49 個。
- copy_line 必須配合使用者指定的圖庫貼上格式；若沒有指定，使用 title<TAB>description<TAB>keywords。
"""

METADATA_JSON_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "title": {"type": "string"},
        "description": {"type": "string"},
        "zh_summary": {"type": "string"},
        "keywords": {"type": "array", "items": {"type": "string"}},
        "keyword_groups": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "language": {"type": "string"},
                    "keywords": {"type": "array", "items": {"type": "string"}},
                    "copy_line": {"type": "string"},
                    "notes": {"type": "string"},
                },
                "required": ["name", "language", "keywords", "copy_line", "notes"],
                "additionalProperties": False,
            },
        },
        "categories": {"type": "array", "items": {"type": "string"}},
        "notes": {"type": "string"},
        "copy_line": {"type": "string"},
    },
    "required": [
        "title",
        "description",
        "zh_summary",
        "keywords",
        "keyword_groups",
        "categories",
        "notes",
        "copy_line",
    ],
    "additionalProperties": False,
}


@dataclass
class RunConfig:
    folder: Path
    provider: str
    model: str
    api_key: str
    prompt: str
    output_dir: Optional[Path] = None
    save_outputs: bool = True
    max_images: int = MAX_IMAGES
    max_side: int = 1600
    max_file_mb: int = DEFAULT_MAX_FILE_MB
    timeout_seconds: int = 180
    retry_count: int = DEFAULT_RETRY_COUNT
    daily_limit: int = DEFAULT_DAILY_API_LIMIT
    reuse_similar_images: bool = DEFAULT_REUSE_SIMILAR_IMAGES
    similar_threshold: int = DEFAULT_SIMILARITY_THRESHOLD
    usage_start_count: int = 0
    api_attempts_this_run: int = 0


@dataclass
class ImageResult:
    index: int
    filename: str
    source_path: str
    status: str
    provider: str
    model: str
    title: str = ""
    description: str = ""
    zh_summary: str = ""
    keywords: Optional[list[str]] = None
    keyword_groups: Optional[list[dict[str, Any]]] = None
    categories: Optional[list[str]] = None
    notes: str = ""
    copy_line: str = ""
    error: str = ""
    thumbnail: str = ""
    prompt_signature: str = ""

    def csv_row(self) -> dict[str, str]:
        return {
            "index": str(self.index),
            "filename": self.filename,
            "status": self.status,
            "title": self.title,
            "description": self.description,
            "zh_summary": self.zh_summary,
            "keywords": ", ".join(self.keywords or []),
            "keyword_groups": json.dumps(self.keyword_groups or [], ensure_ascii=False),
            "categories": ", ".join(self.categories or []),
            "notes": self.notes,
            "copy_line": self.copy_line,
            "error": redact_sensitive(self.error),
            "provider": self.provider,
            "model": self.model,
            "source_path": self.source_path,
            "thumbnail": self.thumbnail,
        }


class ModelOutputFormatError(ValueError):
    """Raised when a model response cannot be parsed as metadata JSON."""


ProgressCallback = Callable[[str, Any], None]


class UsageLimitError(RuntimeError):
    pass


class APIHTTPError(RuntimeError):
    def __init__(self, status_code: int, body: str):
        self.status_code = status_code
        self.body = body
        super().__init__(format_http_error(status_code, body))


def format_http_error(status_code: int, body: str) -> str:
    text = body[:1200]
    lowered = text.lower()
    if status_code == 503 and ("high demand" in lowered or "unavailable" in lowered):
        return (
            "模型目前滿載（HTTP 503 high demand）。"
            "程式會用較長等待重試；若連續滿載會先暫停，稍後可按「繼續未完成」。"
        )
    if status_code == 429 or "rate limit" in lowered or "quota" in lowered:
        return "API 速率或額度暫時受限。請稍後按「繼續未完成」，或降低批次量後再試。"
    return redact_sensitive(f"HTTP {status_code}: {text}")


def is_capacity_error(exc: BaseException) -> bool:
    if isinstance(exc, APIHTTPError):
        body = exc.body.lower()
        return exc.status_code in {429, 500, 503, 529} and any(
            marker in body
            for marker in (
                "high demand",
                "unavailable",
                "overloaded",
                "rate limit",
                "resource_exhausted",
                "try again later",
            )
        )
    text = str(exc).lower()
    return any(marker in text for marker in ("http 503", "high demand", "unavailable", "rate limit"))


def retry_wait_seconds(exc: BaseException, attempt: int) -> int:
    if is_capacity_error(exc):
        return CAPACITY_RETRY_DELAYS[min(attempt, len(CAPACITY_RETRY_DELAYS) - 1)]
    if isinstance(exc, ModelOutputFormatError):
        return 1 + attempt
    return 2 + attempt * 2


def wait_for_retry(seconds: int, stop_event: Optional[threading.Event]) -> None:
    deadline = time.time() + seconds
    while time.time() < deadline:
        if stop_event and stop_event.is_set():
            return
        time.sleep(min(0.5, max(deadline - time.time(), 0)))


def current_progress_payload(
    phase: str,
    filename: str = "",
    index: int = 0,
    total: int = 0,
    attempt: int = 0,
    max_attempts: int = 0,
    retry_until: float = 0,
) -> dict[str, Any]:
    now = time.time()
    return {
        "phase": phase,
        "filename": filename,
        "index": index,
        "total": total,
        "attempt": attempt,
        "max_attempts": max_attempts,
        "retry_until": retry_until,
        "started_at": now,
        "updated_at": now,
    }


def provider_for_model(model: str) -> str:
    text = normalize_model_alias(model).strip().lower()
    if text.startswith("gemini-"):
        return "gemini"
    if text.startswith(("gpt-", "o1", "o3", "o4", "o5", "chatgpt-")):
        return "openai"
    return ""


def normalize_model_alias(model: str) -> str:
    text = re.sub(r"\s+", " ", model.strip())
    if not text:
        return ""
    key = text.casefold()
    compact_key = re.sub(r"[\s_]+", "", key)
    return MODEL_ALIASES.get(key) or MODEL_ALIASES.get(compact_key) or text


def normalize_model_for_provider(provider: str, model: str) -> str:
    provider = provider.strip().lower()
    text = normalize_model_alias(model)
    if not text:
        return PROVIDER_DEFAULT_MODELS.get(provider, text)
    detected_provider = provider_for_model(text)
    if detected_provider and detected_provider != provider:
        return PROVIDER_DEFAULT_MODELS.get(provider, text)
    return text


def redact_sensitive(value: Any, secrets: Optional[list[str]] = None) -> str:
    text = str(value)
    for secret in secrets or []:
        secret = secret.strip()
        if len(secret) >= 6:
            text = text.replace(secret, "[REDACTED]")

    patterns = [
        (r"Bearer\s+[A-Za-z0-9._\-]+", "Bearer [REDACTED]"),
        (r"sk-[A-Za-z0-9][A-Za-z0-9._\-]{8,}", "sk-[REDACTED]"),
        (r"AIza[0-9A-Za-z_\-]{20,}", "AIza[REDACTED]"),
        (r"(?i)(api[_-]?key['\"]?\s*[:=]\s*['\"]?)[^'\"\s,}]+", r"\1[REDACTED]"),
        (r"(?i)(x-goog-api-key['\"]?\s*[:=]\s*['\"]?)[^'\"\s,}]+", r"\1[REDACTED]"),
        (r"(?i)(authorization['\"]?\s*[:=]\s*['\"]?)[^'\"\s,}]+", r"\1[REDACTED]"),
    ]
    for pattern, replacement in patterns:
        text = re.sub(pattern, replacement, text)
    return text


def get_usage_date() -> str:
    return dt.date.today().isoformat()


def usage_model_key(config: RunConfig) -> str:
    model = re.sub(r"[^A-Za-z0-9._-]+", "_", config.model.strip()) or "unknown"
    return f"{config.provider}:{model}"


def load_usage_ledger() -> dict[str, Any]:
    try:
        if USAGE_PATH.exists():
            data = json.loads(USAGE_PATH.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                return data
    except Exception:
        pass
    return {}


def save_usage_ledger(ledger: dict[str, Any]) -> None:
    try:
        USAGE_PATH.parent.mkdir(parents=True, exist_ok=True)
        temp_path = USAGE_PATH.with_suffix(".tmp")
        temp_path.write_text(json.dumps(ledger, ensure_ascii=False, indent=2), encoding="utf-8")
        temp_path.replace(USAGE_PATH)
    except Exception:
        pass


def get_persisted_today_api_attempts(config: RunConfig) -> int:
    ledger = load_usage_ledger()
    today = ledger.get(get_usage_date(), {})
    if not isinstance(today, dict):
        return 0
    total = 0
    for value in today.values():
        try:
            total += int(value)
        except Exception:
            continue
    return total


def get_today_api_attempts(config: RunConfig) -> int:
    return config.usage_start_count + config.api_attempts_this_run


def get_daily_remaining(config: RunConfig) -> int:
    return max(config.daily_limit - get_today_api_attempts(config), 0)


def ensure_daily_limit(config: RunConfig, next_calls: int = 1) -> None:
    if config.daily_limit < 1:
        return
    used = get_today_api_attempts(config)
    if used + next_calls > config.daily_limit:
        raise UsageLimitError(
            f"今日 API request 上限已達 {config.daily_limit}。目前已記錄 {used} 次。"
        )


def record_api_attempt(config: RunConfig) -> None:
    config.api_attempts_this_run += 1
    ledger = load_usage_ledger()
    today_key = get_usage_date()
    model_key = usage_model_key(config)
    day = ledger.setdefault(today_key, {})
    if not isinstance(day, dict):
        day = {}
        ledger[today_key] = day
    try:
        day[model_key] = int(day.get(model_key, 0)) + 1
    except Exception:
        day[model_key] = 1
    save_usage_ledger(ledger)


def discover_images(folder: Path, max_images: int) -> list[Path]:
    if not folder.exists() or not folder.is_dir():
        raise ValueError(f"照片資料夾不存在：{folder}")

    images = sorted(
        [
            path
            for path in folder.iterdir()
            if path.is_file() and path.suffix.lower() in IMAGE_EXTENSIONS
        ],
        key=lambda path: path.name.lower(),
    )
    if len(images) > max_images:
        raise ValueError(
            f"資料夾內有 {len(images)} 張支援格式照片，超過本次設定上限 {max_images} 張。"
        )
    if not images:
        raise ValueError("資料夾內找不到支援格式照片。")
    return images


def file_size_mb(path: Path) -> float:
    return path.stat().st_size / (1024 * 1024)


def is_within_file_limit(path: Path, max_file_mb: int) -> bool:
    try:
        return file_size_mb(path) <= max_file_mb
    except OSError:
        return False


def count_api_eligible_images(images: list[Path], max_file_mb: int) -> int:
    return len([path for path in images if is_within_file_limit(path, max_file_mb)])


def preflight_api_call_count(config: RunConfig, images: list[Path]) -> int:
    eligible_count = count_api_eligible_images(images, config.max_file_mb)
    if config.reuse_similar_images and eligible_count:
        return 1
    return eligible_count


def metadata_signature_for_config(config: RunConfig) -> str:
    payload = {
        "schema_version": METADATA_SCHEMA_VERSION,
        "provider": config.provider.strip().lower(),
        "model": normalize_model_for_provider(config.provider, config.model),
        "prompt": config.prompt.strip(),
    }
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def result_matches_config(result: ImageResult, config: RunConfig) -> bool:
    return bool(result.prompt_signature) and result.prompt_signature == metadata_signature_for_config(config)


def completed_source_token(source_key: str, prompt_signature: str) -> str:
    return f"{prompt_signature}:{source_key}"


def completed_source_token_for_result(result: ImageResult) -> str:
    source_key = source_key_for_result(result)
    if not source_key:
        return ""
    if not result.prompt_signature:
        return source_key
    return completed_source_token(source_key, result.prompt_signature)


def remove_completed_source_for_result(completed_sources: set[str], result: ImageResult) -> None:
    source_key = source_key_for_result(result)
    if not source_key:
        return
    completed_sources.difference_update(
        {
            item
            for item in completed_sources
            if item == source_key or item.endswith(f":{source_key}")
        }
    )


def completed_source_paths_for_config(completed_sources: set[str], config: RunConfig) -> set[str]:
    signature = metadata_signature_for_config(config)
    prefix = f"{signature}:"
    return {item[len(prefix) :] for item in completed_sources if item.startswith(prefix)}


def file_limit_error(path: Path, max_file_mb: int) -> str:
    try:
        size_text = f"{file_size_mb(path):.1f} MB"
    except OSError:
        size_text = "未知大小"
    return f"單檔大小 {size_text} 超過上限 {max_file_mb} MB，已跳過且未呼叫 API。"


def image_similarity_hash(path: Path, hash_size: int = 8) -> Optional[int]:
    if not PIL_AVAILABLE:
        return None
    try:
        with Image.open(path) as image:
            image = ImageOps.exif_transpose(image).convert("L")
            image = image.resize((hash_size + 1, hash_size), Image.Resampling.LANCZOS)
            pixels = list(image.getdata())
    except Exception:
        return None

    value = 0
    bit = 0
    width = hash_size + 1
    for row in range(hash_size):
        row_offset = row * width
        for col in range(hash_size):
            if pixels[row_offset + col] > pixels[row_offset + col + 1]:
                value |= 1 << bit
            bit += 1
    return value


def hash_distance(left: int, right: int) -> int:
    return bin(int(left) ^ int(right)).count("1")


def clone_metadata_from_similar(result: ImageResult, source: ImageResult, distance: int) -> None:
    result.title = source.title
    result.description = source.description
    result.zh_summary = (
        f"與 {source.filename} 相似：{source.zh_summary}"
        if source.zh_summary
        else f"與 {source.filename} 相似，已沿用 metadata。"
    )
    result.keywords = list(source.keywords or [])
    result.keyword_groups = [dict(group) for group in (source.keyword_groups or [])]
    result.categories = list(source.categories or [])
    result.copy_line = source.copy_line
    result.notes = (
        f"本機判定與 {source.filename} 高度相似，已沿用 metadata 節省 API/token。"
        f"相似距離 {distance}，請上架前快速確認。"
    )


def seed_reuse_signatures(results: list[ImageResult], config: RunConfig) -> list[tuple[int, ImageResult]]:
    signatures: list[tuple[int, ImageResult]] = []
    for result in results:
        if result.status != "ok" or not result.source_path or not result_matches_config(result, config):
            continue
        signature = image_similarity_hash(Path(result.source_path))
        if signature is not None:
            signatures.append((signature, result))
    return signatures


def get_effective_api_key(provider: str, api_key: str) -> str:
    key = api_key.strip()
    if key:
        return key
    env_name = "OPENAI_API_KEY" if provider == "openai" else "GEMINI_API_KEY"
    return os.environ.get(env_name, "").strip()


def build_metadata_prompt(user_prompt: str, filename: str, strict_json_retry: bool = False) -> str:
    prompt = user_prompt.strip() or DEFAULT_PROMPT
    retry_note = ""
    if strict_json_retry:
        retry_note = """
前一次模型回應無法被程式解析。這次必須輸出完整有效 JSON：
- 第一個字元必須是 {，最後一個字元必須是 }。
- 不要 markdown，不要註解，不要在 JSON 前後加入任何文字。
- 字串內需要換行時請使用 \\n，不要直接換行。
- 必須保留 title、description、zh_summary、keywords、keyword_groups、categories、notes、copy_line 這些 key。
- keyword_groups 若只有一組 keywords，也請放一組。
- 沒有資料時請用空字串或空陣列，不要省略 key。
"""
    return f"""你是專業圖庫照片 metadata 標注員。請根據圖片內容與使用者需求產生可上架的資料。

使用者需求：
{prompt}

{KEYWORD_OPTIMIZATION_GUIDE}
{retry_note}

檔名：{filename}

請只輸出一個 JSON object，不要 markdown，不要額外說明。JSON schema：
{{
  "title": "string",
  "description": "string",
  "zh_summary": "繁體中文一句話，說明照片主體與場景，只供使用者辨識照片，不放入圖庫 metadata",
  "keywords": ["依使用者指定語言與圖庫排序的 keyword 1", "keyword 2"],
  "keyword_groups": [
    {{
      "name": "圖庫或規則名稱，例如 Adobe Stock / 日本圖庫",
      "language": "該組 keywords 的語言，例如 English / Japanese",
      "keywords": ["該組 keyword 1", "keyword 2"],
      "copy_line": "該組可直接貼上圖庫的內容",
      "notes": "該組規則的提醒；沒有則空字串"
    }}
  ],
  "categories": ["category 1", "category 2"],
  "notes": "string",
  "copy_line": "title<TAB>description<TAB>keyword1, keyword2, keyword3"
}}
"""


def prompt_with_user_correction(base_prompt: str, filename: str, correction: str) -> str:
    clean_correction = correction.strip()
    return f"""{base_prompt.strip() or DEFAULT_PROMPT}

針對檔名 {filename} 的使用者修正資訊：
{clean_correction}

請以這段使用者修正資訊為最高優先。若修正資訊指出主體、菜名、地點、人物、物件或錯誤辨識，請依修正後的內容重新產生 title、description、zh_summary、keyword_groups、keywords 與 copy_line，不要沿用舊的錯誤辨識。"""


def load_image_for_api(path: Path, max_side: int) -> tuple[str, bytes]:
    """Return a compact image payload. Pillow is optional but strongly preferred."""
    if PIL_AVAILABLE:
        try:
            with Image.open(path) as image:
                image = ImageOps.exif_transpose(image)
                if image.mode in {"RGBA", "LA"} or "transparency" in image.info:
                    rgba = image.convert("RGBA")
                    background = Image.new("RGB", rgba.size, (255, 255, 255))
                    background.paste(rgba, mask=rgba.getchannel("A"))
                    image = background
                else:
                    image = image.convert("RGB")

                if max_side > 0:
                    image.thumbnail((max_side, max_side), Image.Resampling.LANCZOS)

                buffer = BytesIO()
                image.save(buffer, format="JPEG", quality=88, optimize=True)
                return "image/jpeg", buffer.getvalue()
        except Exception:
            pass

    mime_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    return mime_type, path.read_bytes()


def post_json(
    url: str,
    payload: dict[str, Any],
    headers: dict[str, str],
    timeout_seconds: int,
) -> dict[str, Any]:
    body = json.dumps(payload).encode("utf-8")
    request_headers = {
        "Content-Type": "application/json",
        **headers,
    }
    request = urllib.request.Request(
        url,
        data=body,
        headers=request_headers,
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
            response_body = response.read().decode("utf-8")
            return json.loads(response_body)
    except urllib.error.HTTPError as exc:
        error_body = exc.read().decode("utf-8", errors="replace")
        raise APIHTTPError(exc.code, error_body) from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(redact_sensitive(f"連線失敗：{exc.reason}")) from exc


def extract_openai_text(response: dict[str, Any]) -> str:
    if isinstance(response.get("output_text"), str):
        return response["output_text"]

    parts: list[str] = []
    for item in response.get("output", []) or []:
        for content in item.get("content", []) or []:
            if isinstance(content.get("text"), str):
                parts.append(content["text"])
            elif content.get("type") == "output_text" and isinstance(content.get("text"), str):
                parts.append(content["text"])
    if parts:
        return "\n".join(parts)
    raise ValueError("OpenAI 回應中找不到文字輸出。")


def extract_gemini_text(response: dict[str, Any]) -> str:
    parts: list[str] = []
    for candidate in response.get("candidates", []) or []:
        content = candidate.get("content", {}) or {}
        for part in content.get("parts", []) or []:
            if isinstance(part.get("text"), str):
                parts.append(part["text"])
    if parts:
        return "\n".join(parts)
    raise ValueError("Gemini 回應中找不到文字輸出。")


def call_openai(
    image_path: Path,
    prompt: str,
    model: str,
    api_key: str,
    max_side: int,
    timeout_seconds: int,
) -> dict[str, Any]:
    mime_type, image_bytes = load_image_for_api(image_path, max_side)
    image_data = base64.b64encode(image_bytes).decode("ascii")
    payload = {
        "model": model,
        "input": [
            {
                "role": "user",
                "content": [
                    {"type": "input_text", "text": prompt},
                    {
                        "type": "input_image",
                        "image_url": f"data:{mime_type};base64,{image_data}",
                    },
                ],
            }
        ],
        "text": {
            "format": {
                "type": "json_schema",
                "name": "stock_photo_metadata",
                "schema": METADATA_JSON_SCHEMA,
                "strict": True,
            }
        },
        "max_output_tokens": MODEL_MAX_OUTPUT_TOKENS,
    }
    response = post_json(
        "https://api.openai.com/v1/responses",
        payload,
        {"Authorization": f"Bearer {api_key}"},
        timeout_seconds,
    )
    return parse_model_json(extract_openai_text(response))


def call_gemini(
    image_path: Path,
    prompt: str,
    model: str,
    api_key: str,
    max_side: int,
    timeout_seconds: int,
) -> dict[str, Any]:
    mime_type, image_bytes = load_image_for_api(image_path, max_side)
    image_data = base64.b64encode(image_bytes).decode("ascii")
    quoted_model = urllib.parse.quote(model, safe="")
    payload = {
        "contents": [
            {
                "role": "user",
                "parts": [
                    {"text": prompt},
                    {
                        "inline_data": {
                            "mime_type": mime_type,
                            "data": image_data,
                        }
                    },
                ],
            }
        ],
        "generationConfig": {
            "responseMimeType": "application/json",
            "responseJsonSchema": METADATA_JSON_SCHEMA,
            "maxOutputTokens": MODEL_MAX_OUTPUT_TOKENS,
            "temperature": 0.2,
        },
    }
    response = post_json(
        f"https://generativelanguage.googleapis.com/v1beta/models/{quoted_model}:generateContent",
        payload,
        {"x-goog-api-key": api_key},
        timeout_seconds,
    )
    return parse_model_json(extract_gemini_text(response))


def parse_model_json(text: str) -> dict[str, Any]:
    raw = text.strip()
    if raw.startswith("```"):
        raw = re.sub(r"^```(?:json)?\s*", "", raw, flags=re.IGNORECASE)
        raw = re.sub(r"\s*```$", "", raw)

    decoder = json.JSONDecoder()
    try:
        parsed, _ = decoder.raw_decode(raw)
    except json.JSONDecodeError as first_error:
        start = raw.find("{")
        if start == -1:
            raise ModelOutputFormatError(f"模型回應不是 JSON：{raw[:300]}") from first_error
        try:
            parsed, _ = decoder.raw_decode(raw[start:])
        except json.JSONDecodeError as exc:
            raise ModelOutputFormatError(
                f"模型回應不是完整有效 JSON：{raw[:300]}"
            ) from exc

    if isinstance(parsed, list):
        if not parsed:
            raise ModelOutputFormatError("模型回應 JSON list 為空。")
        parsed = parsed[0]
    if not isinstance(parsed, dict):
        raise ModelOutputFormatError("模型回應 JSON 不是 object。")
    return parsed


def split_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        items = value
    else:
        items = re.split(r"[,;，、\n]+", str(value))

    cleaned: list[str] = []
    seen: set[str] = set()
    for item in items:
        text = re.sub(r"\s+", " ", str(item).strip().strip("#"))
        if not text:
            continue
        key = text.casefold()
        if key in seen:
            continue
        seen.add(key)
        cleaned.append(text)
    return cleaned


def normalize_keyword_group(
    value: Any,
    index: int,
    title: str,
    description: str,
) -> Optional[dict[str, Any]]:
    if not isinstance(value, dict):
        return None
    keywords = split_list(value.get("keywords"))
    if not keywords:
        return None
    name = str(
        value.get("name")
        or value.get("gallery")
        or value.get("platform")
        or value.get("label")
        or f"Keywords {index}"
    ).strip()
    language = str(value.get("language", "")).strip()
    notes = str(value.get("notes", "")).strip()
    copy_line = str(value.get("copy_line", "")).strip()
    if not copy_line:
        copy_line = f"{title}\t{description}\t{', '.join(keywords)}"
    return {
        "name": name or f"Keywords {index}",
        "language": language,
        "keywords": keywords,
        "copy_line": copy_line,
        "notes": notes,
    }


def normalize_keyword_groups(
    value: Any,
    title: str,
    description: str,
    fallback_keywords: list[str],
    fallback_copy_line: str,
) -> list[dict[str, Any]]:
    groups: list[dict[str, Any]] = []
    items = value if isinstance(value, list) else []
    for index, item in enumerate(items, start=1):
        group = normalize_keyword_group(item, index, title, description)
        if group is not None:
            groups.append(group)
    if not groups and fallback_keywords:
        groups.append(
            {
                "name": "Keywords",
                "language": "",
                "keywords": list(fallback_keywords),
                "copy_line": fallback_copy_line or f"{title}\t{description}\t{', '.join(fallback_keywords)}",
                "notes": "",
            }
        )
    return groups


def normalize_metadata(data: dict[str, Any]) -> dict[str, Any]:
    title = str(data.get("title", "")).strip()
    description = str(data.get("description", "")).strip()
    zh_summary = str(data.get("zh_summary", "")).strip()
    keywords = split_list(data.get("keywords"))
    categories = split_list(data.get("categories"))
    notes = str(data.get("notes", "")).strip()
    copy_line = str(data.get("copy_line", "")).strip()
    keyword_groups = normalize_keyword_groups(
        data.get("keyword_groups"),
        title,
        description,
        keywords,
        copy_line,
    )
    if not keywords and keyword_groups:
        keywords = list(keyword_groups[0].get("keywords", []))
    if not copy_line and keyword_groups:
        copy_line = str(keyword_groups[0].get("copy_line", "")).strip()
    if not copy_line:
        copy_line = f"{title}\t{description}\t{', '.join(keywords)}"
    if not zh_summary:
        zh_summary = f"照片內容請參考英文標題：{title}" if title else "尚未產生中文說明。"
    return {
        "title": title,
        "description": description,
        "zh_summary": zh_summary,
        "keywords": keywords,
        "keyword_groups": keyword_groups,
        "categories": categories,
        "notes": notes,
        "copy_line": copy_line,
    }


def analyze_one_image(
    config: RunConfig,
    image_path: Path,
    api_key: str,
    strict_json_retry: bool = False,
) -> dict[str, Any]:
    prompt = build_metadata_prompt(config.prompt, image_path.name, strict_json_retry)
    if config.provider == "openai":
        return call_openai(
            image_path,
            prompt,
            config.model,
            api_key,
            config.max_side,
            config.timeout_seconds,
        )
    if config.provider == "gemini":
        return call_gemini(
            image_path,
            prompt,
            config.model,
            api_key,
            config.max_side,
            config.timeout_seconds,
        )
    raise ValueError(f"不支援的 provider：{config.provider}")


def prepare_run(config: RunConfig) -> str:
    provider = config.provider.lower().strip()
    if provider not in PROVIDER_DEFAULT_MODELS:
        raise ValueError("Provider 必須是 openai 或 gemini。")
    config.provider = provider
    config.model = normalize_model_for_provider(config.provider, config.model)

    if config.max_images < 1 or config.max_images > MAX_IMAGES:
        raise ValueError(f"上限需介於 1 到 {MAX_IMAGES}。")
    if config.max_side < 512 or config.max_side > 4096:
        raise ValueError("送出長邊需介於 512 到 4096。")
    if config.max_file_mb < 1 or config.max_file_mb > MAX_FILE_MB:
        raise ValueError(f"單檔上限需介於 1 到 {MAX_FILE_MB} MB。")
    if config.timeout_seconds < 10 or config.timeout_seconds > 600:
        raise ValueError("Timeout 需介於 10 到 600 秒。")
    if config.retry_count < 0 or config.retry_count > MAX_RETRY_COUNT:
        raise ValueError(f"重試次數需介於 0 到 {MAX_RETRY_COUNT}。")
    if config.similar_threshold < 0 or config.similar_threshold > 16:
        raise ValueError("相似圖沿用門檻需介於 0 到 16。")
    if config.daily_limit < 1 or config.daily_limit > MAX_DAILY_API_LIMIT:
        raise ValueError(f"每日 API request 上限需介於 1 到 {MAX_DAILY_API_LIMIT}。")

    api_key = get_effective_api_key(config.provider, config.api_key)
    if not api_key:
        env_name = "OPENAI_API_KEY" if config.provider == "openai" else "GEMINI_API_KEY"
        raise ValueError(f"請輸入 API key，或先設定環境變數 {env_name}。")
    config.usage_start_count = get_persisted_today_api_attempts(config)
    config.api_attempts_this_run = 0
    return api_key


def analyze_images(
    config: RunConfig,
    images: list[Path],
    api_key: str,
    progress: Optional[ProgressCallback] = None,
    stop_event: Optional[threading.Event] = None,
    start_index: int = 1,
    completed_before: int = 0,
    total_count: Optional[int] = None,
    reuse_candidates: Optional[list[ImageResult]] = None,
) -> list[ImageResult]:
    results: list[ImageResult] = []
    total = total_count or len(images)
    capacity_error_streak = 0
    metadata_signature = metadata_signature_for_config(config)
    reuse_signatures = seed_reuse_signatures(reuse_candidates or [], config)
    for offset, image_path in enumerate(images):
        index = start_index + offset
        if stop_event and stop_event.is_set():
            if progress:
                progress("log", "已停止，正在輸出目前完成的結果。")
            break

        if progress:
            progress("log", f"[{index}/{total}] 分析 {image_path.name}")
            progress(
                "current",
                current_progress_payload(
                    "準備分析",
                    image_path.name,
                    index,
                    total,
                    max_attempts=config.retry_count + 1,
                ),
            )

        result = ImageResult(
            index=index,
            filename=image_path.name,
            source_path=str(image_path),
            status="ok",
            provider=config.provider,
            model=config.model,
            keywords=[],
            keyword_groups=[],
            categories=[],
            prompt_signature=metadata_signature,
        )

        if not is_within_file_limit(image_path, config.max_file_mb):
            result.status = "error"
            result.error = file_limit_error(image_path, config.max_file_mb)
            results.append(result)
            if progress:
                progress(
                    "current",
                    current_progress_payload("跳過過大檔案", image_path.name, index, total),
                )
                progress("result", result)
                progress("progress", {"done": completed_before + len(results), "total": total})
                progress("log", f"  跳過 {image_path.name}：{result.error}")
            continue

        if config.reuse_similar_images:
            signature = image_similarity_hash(image_path)
            if signature is not None:
                similar_match: Optional[tuple[int, ImageResult]] = None
                for previous_signature, previous_result in reuse_signatures:
                    distance = hash_distance(signature, previous_signature)
                    if distance <= config.similar_threshold:
                        if similar_match is None or distance < similar_match[0]:
                            similar_match = (distance, previous_result)
                if similar_match is not None:
                    distance, source_result = similar_match
                    clone_metadata_from_similar(result, source_result, distance)
                    results.append(result)
                    reuse_signatures.append((signature, result))
                    capacity_error_streak = 0
                    if progress:
                        progress(
                            "current",
                            current_progress_payload("沿用相似照片 metadata", image_path.name, index, total),
                        )
                        progress(
                            "log",
                            f"  沿用 {source_result.filename} 的 metadata，未呼叫 API：{image_path.name}",
                        )
                        progress("result", result)
                        progress("progress", {"done": completed_before + len(results), "total": total})
                    continue

        try:
            raw_metadata: Optional[dict[str, Any]] = None
            last_error: Optional[Exception] = None
            for attempt in range(config.retry_count + 1):
                try:
                    ensure_daily_limit(config, 1)
                    record_api_attempt(config)
                    if progress:
                        phase = "送出 API 分析中" if attempt == 0 else "重新送出 API"
                        progress(
                            "current",
                            current_progress_payload(
                                phase,
                                image_path.name,
                                index,
                                total,
                                attempt=attempt + 1,
                                max_attempts=config.retry_count + 1,
                            ),
                        )
                    raw_metadata = analyze_one_image(
                        config,
                        image_path,
                        api_key,
                        strict_json_retry=isinstance(last_error, ModelOutputFormatError),
                    )
                    break
                except UsageLimitError:
                    raise
                except Exception as exc:
                    last_error = exc
                    if attempt < config.retry_count:
                        wait_seconds = retry_wait_seconds(exc, attempt)
                        retry_until = time.time() + wait_seconds
                        if progress:
                            progress(
                                "current",
                                current_progress_payload(
                                    "等待重試",
                                    image_path.name,
                                    index,
                                    total,
                                    attempt=attempt + 1,
                                    max_attempts=config.retry_count + 1,
                                    retry_until=retry_until,
                                ),
                            )
                            progress(
                                "log",
                                f"  重試 {attempt + 1}/{config.retry_count}："
                                f"{redact_sensitive(exc, [api_key, config.api_key])} "
                                f"等待 {wait_seconds} 秒",
                            )
                        wait_for_retry(wait_seconds, stop_event)
                        if stop_event and stop_event.is_set():
                            break
            if raw_metadata is None:
                raise last_error or RuntimeError("分析失敗。")
            normalized = normalize_metadata(raw_metadata)
            result.title = normalized["title"]
            result.description = normalized["description"]
            result.zh_summary = normalized["zh_summary"]
            result.keywords = normalized["keywords"]
            result.keyword_groups = normalized["keyword_groups"]
            result.categories = normalized["categories"]
            result.notes = normalized["notes"]
            result.copy_line = normalized["copy_line"]
            capacity_error_streak = 0
            signature = image_similarity_hash(image_path) if config.reuse_similar_images else None
            if signature is not None:
                reuse_signatures.append((signature, result))
            if progress:
                progress(
                    "current",
                    current_progress_payload("完成本張", image_path.name, index, total),
                )
        except UsageLimitError as exc:
            result.status = "error"
            result.error = redact_sensitive(exc, [api_key, config.api_key])
            results.append(result)
            if progress:
                progress(
                    "current",
                    current_progress_payload("已達 API 上限", image_path.name, index, total),
                )
                progress("result", result)
                progress("progress", {"done": completed_before + len(results), "total": total})
                progress("log", result.error)
            break
        except Exception as exc:
            result.status = "error"
            result.error = redact_sensitive(exc, [api_key, config.api_key])
            if is_capacity_error(exc):
                capacity_error_streak += 1
            else:
                capacity_error_streak = 0
            if progress:
                progress(
                    "current",
                    current_progress_payload("本張失敗", image_path.name, index, total),
                )

        results.append(result)
        if progress:
            progress("result", result)
            progress("progress", {"done": completed_before + len(results), "total": total})
            if capacity_error_streak >= CAPACITY_STOP_STREAK:
                progress(
                    "log",
                    f"模型連續 {capacity_error_streak} 張滿載或限流，已先暫停批次。"
                    "請稍後按「繼續未完成」，或改用較穩定的模型。",
                )
                break

    return results


def process_folder(
    config: RunConfig,
    progress: Optional[ProgressCallback] = None,
    stop_event: Optional[threading.Event] = None,
) -> dict[str, Any]:
    api_key = prepare_run(config)
    images = discover_images(config.folder, config.max_images)
    ensure_daily_limit(config, preflight_api_call_count(config, images))
    timestamp = dt.datetime.now().strftime("%Y%m%d_%H%M%S")
    output_dir = (
        config.output_dir or config.folder / f"stock_keyworder_output_{timestamp}"
        if config.save_outputs
        else None
    )

    if progress:
        progress("scan", {"total": len(images), "output_dir": str(output_dir or ""), "mode": "batch"})

    results = analyze_images(
        config,
        images,
        api_key,
        progress=progress,
        stop_event=stop_event,
        total_count=len(images),
    )
    manifest = (
        write_outputs(output_dir, results, config)
        if output_dir
        else build_result_manifest(results, config)
    )
    if progress:
        progress("done", manifest)
    return manifest


def is_file_settled(path: Path, settle_seconds: float) -> bool:
    try:
        stat = path.stat()
    except OSError:
        return False
    return time.time() - stat.st_mtime >= settle_seconds and stat.st_size > 0


def watch_folder(
    config: RunConfig,
    interval_seconds: float = 5.0,
    settle_seconds: float = 3.0,
    progress: Optional[ProgressCallback] = None,
    stop_event: Optional[threading.Event] = None,
) -> dict[str, Any]:
    api_key = prepare_run(config)
    if not config.folder.exists() or not config.folder.is_dir():
        raise ValueError(f"照片資料夾不存在：{config.folder}")

    timestamp = dt.datetime.now().strftime("%Y%m%d_%H%M%S")
    output_dir = (
        config.output_dir or config.folder / f"stock_keyworder_watch_{timestamp}"
        if config.save_outputs
        else None
    )
    results: list[ImageResult] = []
    processed: set[str] = set()
    last_manifest: dict[str, Any] = build_result_manifest(results, config, output_dir)

    if progress:
        progress("scan", {"total": config.max_images, "output_dir": str(output_dir or ""), "mode": "watch"})
        progress("log", f"開始監看：{config.folder}")

    while not (stop_event and stop_event.is_set()):
        all_images = sorted(
            [
                path
                for path in config.folder.iterdir()
                if path.is_file() and path.suffix.lower() in IMAGE_EXTENSIONS
            ],
            key=lambda path: path.name.lower(),
        )
        if len(all_images) > config.max_images:
            raise ValueError(
                f"資料夾內有 {len(all_images)} 張支援格式照片，超過本次設定上限 {config.max_images} 張。"
            )

        candidates = [
            path
            for path in all_images
            if str(path.resolve()) not in processed and is_file_settled(path, settle_seconds)
        ]
        if candidates:
            if progress:
                progress("log", f"偵測到 {len(candidates)} 張新照片。")
            new_results = analyze_images(
                config,
                candidates,
                api_key,
                progress=progress,
                stop_event=stop_event,
                start_index=len(results) + 1,
                completed_before=len(results),
                total_count=config.max_images,
                reuse_candidates=results,
            )
            results.extend(new_results)
            for result in new_results:
                processed.add(str(Path(result.source_path).resolve()))
            last_manifest = (
                write_outputs(output_dir, results, config)
                if output_dir
                else build_result_manifest(results, config)
            )
            if progress:
                progress("saved", last_manifest)

        if len(results) >= config.max_images:
            if progress:
                progress("log", f"已達 {config.max_images} 張上限，停止監看。")
            break

        if progress:
            progress("watch_idle", {"count": len(results)})

        time.sleep(interval_seconds)

    if results and output_dir:
        last_manifest = write_outputs(output_dir, results, config)
    if progress:
        progress("done", last_manifest)
    return last_manifest


def safe_stem(name: str) -> str:
    stem = Path(name).stem
    stem = re.sub(r"[^A-Za-z0-9._-]+", "_", stem).strip("._-")
    return stem[:80] or "image"


def create_thumbnail(source: Path, destination: Path) -> Path:
    destination.parent.mkdir(parents=True, exist_ok=True)
    if PIL_AVAILABLE:
        try:
            with Image.open(source) as image:
                image = ImageOps.exif_transpose(image)
                if image.mode in {"RGBA", "LA"} or "transparency" in image.info:
                    rgba = image.convert("RGBA")
                    background = Image.new("RGB", rgba.size, (255, 255, 255))
                    background.paste(rgba, mask=rgba.getchannel("A"))
                    image = background
                else:
                    image = image.convert("RGB")

                image.thumbnail((260, 180), Image.Resampling.LANCZOS)
                canvas = Image.new("RGB", (260, 180), (246, 247, 249))
                x = (260 - image.width) // 2
                y = (180 - image.height) // 2
                canvas.paste(image, (x, y))
                canvas.save(destination, format="JPEG", quality=84, optimize=True)
                return destination
        except Exception:
            pass

    fallback = destination.with_suffix(source.suffix.lower())
    shutil.copy2(source, fallback)
    return fallback


def create_thumbnail_response(source: Path, size: tuple[int, int] = (150, 104)) -> tuple[str, bytes]:
    if PIL_AVAILABLE:
        try:
            with Image.open(source) as image:
                image = ImageOps.exif_transpose(image)
                if image.mode in {"RGBA", "LA"} or "transparency" in image.info:
                    rgba = image.convert("RGBA")
                    background = Image.new("RGB", rgba.size, (255, 255, 255))
                    background.paste(rgba, mask=rgba.getchannel("A"))
                    image = background
                else:
                    image = image.convert("RGB")

                image.thumbnail(size, Image.Resampling.LANCZOS)
                canvas = Image.new("RGB", size, (246, 247, 249))
                x = (size[0] - image.width) // 2
                y = (size[1] - image.height) // 2
                canvas.paste(image, (x, y))
                buffer = BytesIO()
                canvas.save(buffer, format="JPEG", quality=72, optimize=True)
                return "image/jpeg", buffer.getvalue()
        except Exception:
            pass

    svg = (
        '<svg xmlns="http://www.w3.org/2000/svg" width="150" height="104">'
        '<rect width="150" height="104" fill="#f2f4f7"/>'
        '<text x="75" y="55" text-anchor="middle" font-family="sans-serif" '
        'font-size="12" fill="#667085">No preview</text></svg>'
    )
    return "image/svg+xml; charset=utf-8", svg.encode("utf-8")


def write_outputs(output_dir: Path, results: list[ImageResult], config: RunConfig) -> dict[str, Any]:
    output_dir.mkdir(parents=True, exist_ok=True)
    thumbnail_dir = output_dir / "thumbnails"

    for result in results:
        result.error = redact_sensitive(result.error)
        source = Path(result.source_path)
        thumbnail_name = f"{result.index:03d}_{safe_stem(result.filename)}.jpg"
        thumbnail_path = thumbnail_dir / thumbnail_name
        actual_thumbnail = create_thumbnail(source, thumbnail_path)
        result.thumbnail = str(actual_thumbnail.relative_to(output_dir))

    csv_path = output_dir / "stock_keywords.csv"
    json_path = output_dir / "stock_keywords.json"
    html_path = output_dir / "stock_keywords_report.html"

    fieldnames = [
        "index",
        "filename",
        "status",
        "title",
        "description",
        "zh_summary",
        "keywords",
        "keyword_groups",
        "categories",
        "notes",
        "copy_line",
        "error",
        "provider",
        "model",
        "source_path",
        "thumbnail",
    ]
    with csv_path.open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for result in results:
            writer.writerow(result.csv_row())

    payload = {
        "created_at": dt.datetime.now().isoformat(timespec="seconds"),
        "provider": config.provider,
        "model": config.model,
        "source_folder": str(config.folder),
        "max_images": config.max_images,
        "results": [asdict(result) for result in results],
    }
    json_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    html_path.write_text(
        build_html_report(results, payload),
        encoding="utf-8",
    )
    return {
        "output_dir": str(output_dir),
        "csv": str(csv_path),
        "json": str(json_path),
        "html": str(html_path),
        "count": len(results),
        "ok_count": len([result for result in results if result.status == "ok"]),
        "error_count": len([result for result in results if result.status != "ok"]),
    }


def build_result_manifest(
    results: list[ImageResult],
    config: RunConfig,
    output_dir: Optional[Path] = None,
) -> dict[str, Any]:
    return {
        "output_dir": str(output_dir or ""),
        "csv": "",
        "json": "",
        "html": "",
        "count": len(results),
        "ok_count": len([result for result in results if result.status == "ok"]),
        "error_count": len([result for result in results if result.status != "ok"]),
        "provider": config.provider,
        "model": config.model,
        "source_folder": str(config.folder),
    }


def result_from_dict(data: dict[str, Any], fallback_index: int) -> ImageResult:
    keywords = data.get("keywords")
    categories = data.get("categories")
    title = str(data.get("title", ""))
    description = str(data.get("description", ""))
    normalized_keywords = keywords if isinstance(keywords, list) else split_list(keywords)
    keyword_groups = normalize_keyword_groups(
        data.get("keyword_groups"),
        title,
        description,
        normalized_keywords,
        str(data.get("copy_line", "")),
    )
    return ImageResult(
        index=int(data.get("index") or fallback_index),
        filename=str(data.get("filename", "")),
        source_path=str(data.get("source_path", "")),
        status=str(data.get("status", "ok")),
        provider=str(data.get("provider", "")),
        model=str(data.get("model", "")),
        title=title,
        description=description,
        zh_summary=str(data.get("zh_summary", "")),
        keywords=normalized_keywords,
        keyword_groups=keyword_groups,
        categories=categories if isinstance(categories, list) else split_list(categories),
        notes=str(data.get("notes", "")),
        copy_line=str(data.get("copy_line", "")),
        error=str(data.get("error", "")),
        thumbnail=str(data.get("thumbnail", "")),
        prompt_signature=str(data.get("prompt_signature", "")),
    )


def reindex_results(results: list[ImageResult]) -> list[ImageResult]:
    limited = results[:MAX_IMAGES]
    for index, result in enumerate(limited, start=1):
        result.index = index
    return limited


def source_key_for_path(path: Path) -> str:
    try:
        return str(path.expanduser().resolve())
    except Exception:
        return str(path.expanduser())


def source_key_for_result(result: ImageResult) -> str:
    if result.source_path:
        return source_key_for_path(Path(result.source_path))
    return result.filename.strip().casefold()


def is_completed_result(result: ImageResult) -> bool:
    if result.status == "ok":
        return True
    return result.error.startswith("單檔大小")


def completed_sources_from_results(
    results: list[ImageResult],
    config: Optional[RunConfig] = None,
) -> set[str]:
    return {
        completed_source_token_for_result(result)
        for result in results
        if is_completed_result(result)
        and completed_source_token_for_result(result)
        and (config is None or result_matches_config(result, config))
    }


def discover_remaining_images(
    config: RunConfig,
    results: list[ImageResult],
    completed_sources: Optional[set[str]] = None,
) -> list[Path]:
    images = discover_images(config.folder, config.max_images)
    skip_sources = completed_source_paths_for_config(set(completed_sources or set()), config)
    skip_sources.update(
        source_key_for_result(result)
        for result in results
        if is_completed_result(result) and result_matches_config(result, config) and source_key_for_result(result)
    )
    skip_filenames = {
        result.filename.strip().casefold()
        for result in results
        if is_completed_result(result) and result_matches_config(result, config) and result.filename.strip()
    }
    return [
        path
        for path in images
        if source_key_for_path(path) not in skip_sources and path.name.casefold() not in skip_filenames
    ]


def save_pending_results(
    results: list[ImageResult],
    completed_sources: Optional[set[str]] = None,
) -> dict[str, Any]:
    limited = reindex_results(list(results))
    saved_completed_sources = set(completed_sources or set())
    saved_completed_sources.update(completed_sources_from_results(limited))
    payload = {
        "created_at": dt.datetime.now().isoformat(timespec="seconds"),
        "max_results": MAX_IMAGES,
        "count": len(limited),
        "completed_sources": sorted(saved_completed_sources)[:MAX_IMAGES],
        "results": [asdict(result) for result in limited],
    }
    PENDING_PATH.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    try:
        PENDING_PATH.chmod(0o600)
    except OSError:
        pass
    return {
        "ok": True,
        "path": str(PENDING_PATH),
        "count": len(limited),
        "created_at": payload["created_at"],
    }


def load_pending_state() -> tuple[list[ImageResult], set[str]]:
    if not PENDING_PATH.exists():
        return [], set()
    payload = json.loads(PENDING_PATH.read_text(encoding="utf-8"))
    raw_results = payload.get("results", [])
    if not isinstance(raw_results, list):
        raw_results = []
    results = [
        result_from_dict(item, index)
        for index, item in enumerate(raw_results[:MAX_IMAGES], start=1)
        if isinstance(item, dict)
    ]
    completed_sources_raw = payload.get("completed_sources", [])
    completed_sources_items = (
        completed_sources_raw[:MAX_IMAGES]
        if isinstance(completed_sources_raw, list)
        else []
    )
    completed_sources = {str(item) for item in completed_sources_items if str(item).strip()}
    completed_sources.update(completed_sources_from_results(results))
    return reindex_results(results), completed_sources


def load_pending_results() -> list[ImageResult]:
    results, _ = load_pending_state()
    return results


def pending_results_status() -> dict[str, Any]:
    if not PENDING_PATH.exists():
        return {"exists": False, "count": 0, "path": str(PENDING_PATH), "created_at": ""}
    try:
        payload = json.loads(PENDING_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {"exists": True, "count": 0, "path": str(PENDING_PATH), "created_at": ""}
    raw_results = payload.get("results", [])
    count = len(raw_results) if isinstance(raw_results, list) else int(payload.get("count", 0) or 0)
    return {
        "exists": True,
        "count": min(count, MAX_IMAGES),
        "path": str(PENDING_PATH),
        "created_at": str(payload.get("created_at", "")),
    }


def build_html_report(results: list[ImageResult], payload: dict[str, Any]) -> str:
    rows: list[str] = []
    for result in results:
        keywords = ", ".join(result.keywords or [])
        categories = ", ".join(result.categories or [])
        copy_line = result.copy_line or f"{result.title}\t{result.description}\t{keywords}"
        groups = result.keyword_groups or [
            {
                "name": "Keywords",
                "keywords": result.keywords or [],
                "copy_line": copy_line,
                "language": "",
                "notes": "",
            }
        ]
        group_blocks: list[str] = []
        group_buttons: list[str] = []
        for group in groups:
            group_name = str(group.get("name") or "Keywords")
            group_keywords = split_list(group.get("keywords"))
            group_keyword_text = ", ".join(group_keywords)
            group_copy_line = str(group.get("copy_line") or f"{result.title}\t{result.description}\t{group_keyword_text}")
            group_blocks.append(
                '<div class="keyword-group">'
                f'<div class="muted">{html.escape(group_name)} · {len(group_keywords)} keywords</div>'
                f'<div>{html.escape(group_keyword_text)}</div>'
                '</div>'
            )
            group_buttons.append(
                f'<button data-copy="{html.escape(group_keyword_text, quote=True)}">'
                f'{html.escape(group_name)} 關鍵字({len(group_keywords)})</button>'
            )
            group_buttons.append(
                f'<button data-copy="{html.escape(group_copy_line, quote=True)}">'
                f'{html.escape(group_name)} 整列</button>'
            )
        status_label = "完成" if result.status == "ok" else "錯誤"
        status_class = "ok" if result.status == "ok" else "error"
        rows.append(
            f"""
            <tr class="{status_class}">
              <td class="thumb"><img src="{html.escape(result.thumbnail, quote=True)}" alt=""></td>
              <td>
                <div class="filename">{html.escape(result.filename)}</div>
                <div class="description">{html.escape(result.zh_summary)}</div>
                <div class="muted">{html.escape(result.source_path)}</div>
              </td>
              <td><span class="status {status_class}">{status_label}</span></td>
              <td>
                <div class="title">{html.escape(result.title)}</div>
                <div class="description">{html.escape(result.description)}</div>
                <div class="notes">{html.escape(result.notes or result.error)}</div>
              </td>
              <td>
                <div class="keywords">{"".join(group_blocks)}</div>
                <div class="muted">{html.escape(categories)}</div>
              </td>
              <td class="actions">
                <button data-copy="{html.escape(result.title, quote=True)}">複製標題</button>
                <button data-copy="{html.escape(result.description, quote=True)}">複製描述</button>
                <button data-copy="{html.escape(result.title + chr(9) + result.description, quote=True)}">複製標題+描述</button>
                {"".join(group_buttons)}
                <button data-copy="{html.escape(copy_line, quote=True)}">複製整列</button>
              </td>
            </tr>
            """
        )

    ok_count = len([result for result in results if result.status == "ok"])
    error_count = len(results) - ok_count
    created_at = html.escape(str(payload.get("created_at", "")))
    source_folder = html.escape(str(payload.get("source_folder", "")))
    provider = html.escape(str(payload.get("provider", "")))
    model = html.escape(str(payload.get("model", "")))
    return f"""<!doctype html>
<html lang="zh-Hant">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{APP_NAME} Report</title>
  <style>
    :root {{
      color-scheme: light;
      --bg: #f5f7fa;
      --panel: #ffffff;
      --text: #17202a;
      --muted: #667085;
      --line: #d9e0ea;
      --ok: #0f766e;
      --error: #b42318;
      --button: #1f2937;
    }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      background: var(--bg);
      color: var(--text);
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
      font-size: 14px;
      line-height: 1.45;
    }}
    header {{
      padding: 20px 28px 14px;
      border-bottom: 1px solid var(--line);
      background: var(--panel);
      position: sticky;
      top: 0;
      z-index: 2;
    }}
    h1 {{
      margin: 0 0 8px;
      font-size: 22px;
      font-weight: 650;
      letter-spacing: 0;
    }}
    .meta {{
      display: flex;
      flex-wrap: wrap;
      gap: 8px 16px;
      color: var(--muted);
      font-size: 13px;
    }}
    main {{ padding: 18px 28px 32px; }}
    .toolbar {{
      display: flex;
      justify-content: space-between;
      align-items: center;
      gap: 12px;
      margin-bottom: 12px;
    }}
    input[type="search"] {{
      width: min(480px, 100%);
      border: 1px solid var(--line);
      border-radius: 6px;
      padding: 9px 10px;
      font: inherit;
      background: #fff;
    }}
    .counts {{ color: var(--muted); }}
    table {{
      width: 100%;
      border-collapse: collapse;
      background: var(--panel);
      border: 1px solid var(--line);
    }}
    th, td {{
      border-bottom: 1px solid var(--line);
      padding: 10px;
      vertical-align: top;
      text-align: left;
    }}
    th {{
      font-size: 12px;
      color: var(--muted);
      font-weight: 650;
      background: #f8fafc;
      position: sticky;
      top: 86px;
      z-index: 1;
    }}
    tr.error {{ background: #fff7f6; }}
    .thumb {{ width: 280px; }}
    .thumb img {{
      width: 260px;
      height: 180px;
      object-fit: contain;
      background: #f2f4f7;
      border: 1px solid var(--line);
      border-radius: 6px;
      display: block;
    }}
    .filename, .title {{ font-weight: 650; }}
    .description, .keywords, .notes {{ margin-top: 6px; }}
    .keywords {{ max-width: 520px; }}
    .keyword-group {{ margin-bottom: 10px; }}
    .notes {{ color: var(--error); }}
    .muted {{
      color: var(--muted);
      font-size: 12px;
      word-break: break-all;
    }}
    .status {{
      display: inline-block;
      padding: 3px 8px;
      border-radius: 999px;
      font-size: 12px;
      font-weight: 650;
      border: 1px solid currentColor;
    }}
    .status.ok {{ color: var(--ok); }}
    .status.error {{ color: var(--error); }}
    .actions {{
      width: 128px;
      white-space: nowrap;
    }}
    button {{
      display: block;
      width: 108px;
      margin-bottom: 8px;
      border: 0;
      border-radius: 6px;
      padding: 8px 10px;
      line-height: 1.25;
      overflow-wrap: anywhere;
      white-space: normal;
      color: #fff;
      background: var(--button);
      cursor: pointer;
      font: inherit;
      font-size: 13px;
    }}
    button.copied {{ background: var(--ok); }}
    @media (max-width: 900px) {{
      main {{ padding: 12px; }}
      header {{ padding: 16px 12px 12px; position: static; }}
      table, thead, tbody, th, td, tr {{ display: block; }}
      thead {{ display: none; }}
      tr {{ border: 1px solid var(--line); margin-bottom: 12px; background: var(--panel); }}
      td {{ border-bottom: 0; }}
      .thumb, .actions {{ width: auto; }}
      .thumb img {{ width: 100%; height: auto; max-height: 260px; }}
      button {{ display: inline-block; margin-right: 8px; }}
    }}
  </style>
</head>
<body>
  <header>
    <h1>{APP_NAME}</h1>
    <div class="meta">
      <span>{created_at}</span>
      <span>{provider} / {model}</span>
      <span>{source_folder}</span>
    </div>
  </header>
  <main>
    <div class="toolbar">
      <input id="filter" type="search" placeholder="搜尋檔名、標題、關鍵字">
      <div class="counts">完成 {ok_count}，錯誤 {error_count}</div>
    </div>
    <table id="report">
      <thead>
        <tr>
          <th>縮圖</th>
          <th>檔案</th>
          <th>狀態</th>
          <th>標題 / 描述 / 備註</th>
          <th>關鍵字 / 類別</th>
          <th>複製</th>
        </tr>
      </thead>
      <tbody>
        {''.join(rows)}
      </tbody>
    </table>
  </main>
  <script>
    document.addEventListener('click', async (event) => {{
      const button = event.target.closest('button[data-copy]');
      if (!button) return;
      await navigator.clipboard.writeText(button.getAttribute('data-copy') || '');
      const label = button.textContent;
      button.textContent = '已複製';
      button.classList.add('copied');
      setTimeout(() => {{
        button.textContent = label;
        button.classList.remove('copied');
      }}, 900);
    }});

    const filter = document.getElementById('filter');
    filter.addEventListener('input', () => {{
      const term = filter.value.trim().toLowerCase();
      document.querySelectorAll('#report tbody tr').forEach((row) => {{
        row.style.display = row.textContent.toLowerCase().includes(term) ? '' : 'none';
      }});
    }});
  </script>
</body>
</html>
"""


def load_saved_settings() -> dict[str, Any]:
    try:
        if CONFIG_PATH.exists():
            data = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                return data
    except Exception:
        pass
    return {}


def save_settings(settings: dict[str, Any]) -> None:
    safe_settings = dict(settings)
    safe_settings.pop("api_key", None)
    try:
        CONFIG_PATH.write_text(
            json.dumps(safe_settings, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    except Exception:
        pass


def choose_folder_dialog() -> str:
    if sys.platform == "darwin" and shutil.which("osascript"):
        script = 'POSIX path of (choose folder with prompt "選擇照片資料夾")'
        try:
            result = subprocess.run(
                ["osascript", "-e", script],
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                text=True,
                timeout=300,
                check=False,
            )
            return result.stdout.strip() if result.returncode == 0 else ""
        except Exception:
            pass

    if sys.platform.startswith("win"):
        powershell = shutil.which("powershell.exe") or shutil.which("powershell")
        if powershell:
            command = (
                "Add-Type -AssemblyName System.Windows.Forms; "
                "$dialog = New-Object System.Windows.Forms.FolderBrowserDialog; "
                "$dialog.Description = '選擇照片資料夾'; "
                "$dialog.ShowNewFolderButton = $false; "
                "if ($dialog.ShowDialog() -eq [System.Windows.Forms.DialogResult]::OK) "
                "{ [Console]::OutputEncoding = [System.Text.Encoding]::UTF8; "
                "Write-Output $dialog.SelectedPath }"
            )
            try:
                result = subprocess.run(
                    [powershell, "-STA", "-NoProfile", "-Command", command],
                    stdout=subprocess.PIPE,
                    stderr=subprocess.DEVNULL,
                    text=True,
                    timeout=300,
                    check=False,
                )
                return result.stdout.strip() if result.returncode == 0 else ""
            except Exception:
                pass

    if shutil.which("zenity"):
        try:
            result = subprocess.run(
                ["zenity", "--file-selection", "--directory", "--title=選擇照片資料夾"],
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                text=True,
                timeout=300,
                check=False,
            )
            return result.stdout.strip() if result.returncode == 0 else ""
        except Exception:
            pass

    try:
        import tkinter as tk
        from tkinter import filedialog

        root = tk.Tk()
        root.withdraw()
        root.attributes("-topmost", True)
        selected = filedialog.askdirectory(title="選擇照片資料夾")
        root.destroy()
        return selected or ""
    except Exception:
        return ""


def safe_prompt_name(name: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9._\-\u4e00-\u9fff]+", "_", name.strip())
    cleaned = cleaned.strip("._-")
    return cleaned[:80] or "default"


def prompt_file_path(name: str) -> Path:
    return PROMPT_DIR / f"{safe_prompt_name(name)}.txt"


def list_prompt_files() -> list[str]:
    try:
        PROMPT_DIR.mkdir(parents=True, exist_ok=True)
        return sorted(path.stem for path in PROMPT_DIR.glob("*.txt") if path.is_file())
    except Exception:
        return []


def save_prompt_file(name: str, prompt: str) -> Path:
    PROMPT_DIR.mkdir(parents=True, exist_ok=True)
    path = prompt_file_path(name)
    path.write_text(prompt, encoding="utf-8")
    return path


def load_prompt_file(name: str) -> str:
    path = prompt_file_path(name)
    if not path.exists():
        raise ValueError(f"找不到 Prompt 檔：{safe_prompt_name(name)}")
    return path.read_text(encoding="utf-8")


def delete_prompt_file(name: str) -> str:
    safe_name = safe_prompt_name(name)
    if safe_name == "default":
        raise ValueError("請先選擇或輸入要刪除的 Prompt 名稱。")
    path = prompt_file_path(safe_name)
    if not path.exists():
        raise ValueError(f"找不到 Prompt 檔：{safe_name}")
    path.unlink()
    return safe_name


def key_account(provider: str) -> str:
    provider = provider.strip().lower() or "default"
    return f"stock_keyworder_{provider}_api_key"


def keychain_available() -> bool:
    return sys.platform == "darwin" and shutil.which("security") is not None


def save_key_to_keychain(provider: str, api_key: str) -> bool:
    if not keychain_available():
        return False
    account = key_account(provider)
    try:
        subprocess.run(
            ["security", "delete-generic-password", "-s", KEYCHAIN_SERVICE, "-a", account],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=5,
            check=False,
        )
        subprocess.run(
            [
                "security",
                "add-generic-password",
                "-U",
                "-s",
                KEYCHAIN_SERVICE,
                "-a",
                account,
                "-w",
                api_key,
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=8,
            check=True,
        )
        return True
    except Exception:
        return False


def load_key_from_keychain(provider: str) -> str:
    if not keychain_available():
        return ""
    try:
        result = subprocess.run(
            [
                "security",
                "find-generic-password",
                "-s",
                KEYCHAIN_SERVICE,
                "-a",
                key_account(provider),
                "-w",
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            timeout=5,
            check=False,
        )
        if result.returncode == 0:
            return result.stdout.strip()
    except Exception:
        pass
    return ""


def delete_key_from_keychain(provider: str) -> None:
    if not keychain_available():
        return
    try:
        subprocess.run(
            ["security", "delete-generic-password", "-s", KEYCHAIN_SERVICE, "-a", key_account(provider)],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=5,
            check=False,
        )
    except Exception:
        pass


def load_key_cache_file() -> dict[str, str]:
    try:
        if KEY_CACHE_PATH.exists():
            data = json.loads(KEY_CACHE_PATH.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                return {str(key): str(value) for key, value in data.items()}
    except Exception:
        pass
    return {}


def save_key_cache_file(data: dict[str, str]) -> None:
    KEY_CACHE_PATH.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    try:
        os.chmod(KEY_CACHE_PATH, 0o600)
    except Exception:
        pass


def save_cached_api_key(provider: str, api_key: str) -> str:
    if not api_key.strip():
        raise ValueError("API key 是空的，無法暫存。")
    if save_key_to_keychain(provider, api_key):
        return "macOS Keychain"
    data = load_key_cache_file()
    data[provider.strip().lower()] = base64.b64encode(api_key.encode("utf-8")).decode("ascii")
    save_key_cache_file(data)
    return str(KEY_CACHE_PATH)


def load_cached_api_key(provider: str) -> str:
    key = load_key_from_keychain(provider)
    if key:
        return key
    encoded = load_key_cache_file().get(provider.strip().lower(), "")
    if encoded:
        try:
            return base64.b64decode(encoded.encode("ascii")).decode("utf-8")
        except Exception:
            return ""
    return ""


def clear_cached_api_key(provider: str) -> None:
    provider = provider.strip().lower()
    delete_key_from_keychain(provider)
    data = load_key_cache_file()
    if provider in data:
        data.pop(provider, None)
        save_key_cache_file(data)


def cached_api_key_status(provider: str) -> dict[str, Any]:
    provider = provider.strip().lower()
    has_keychain = bool(load_key_from_keychain(provider))
    has_file = provider in load_key_cache_file()
    return {
        "provider": provider,
        "has_key": has_keychain or has_file,
        "storage": "macOS Keychain" if has_keychain else (str(KEY_CACHE_PATH) if has_file else ""),
    }


def find_available_port(start_port: int = 8765) -> int:
    for port in range(start_port, start_port + 50):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                sock.bind(("127.0.0.1", port))
                return port
            except OSError:
                continue
    raise RuntimeError("找不到可用的本機連接埠。")


def build_web_app_html(settings: dict[str, Any]) -> str:
    provider_raw = str(settings.get("provider", "openai")).strip().lower()
    if provider_raw not in PROVIDER_DEFAULT_MODELS:
        provider_raw = "openai"
    model_raw = normalize_model_for_provider(
        provider_raw,
        str(settings.get("model", PROVIDER_DEFAULT_MODELS.get(provider_raw, "gpt-5.5"))),
    )
    provider = html.escape(provider_raw, quote=True)
    model = html.escape(model_raw, quote=True)
    folder = html.escape(str(settings.get("folder", "")), quote=True)
    prompt = html.escape(str(settings.get("prompt", DEFAULT_PROMPT)))
    watch_checked = "checked" if settings.get("watch", False) else ""
    prompt_name = html.escape(str(settings.get("prompt_name", "default")), quote=True)
    return f"""<!doctype html>
<html lang="zh-Hant">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{APP_NAME}</title>
  <style>
    :root {{
      color-scheme: light;
      --bg: #f4f6f8;
      --panel: #ffffff;
      --text: #17202a;
      --muted: #667085;
      --line: #d0d7e2;
      --field: #ffffff;
      --primary: #2563eb;
      --danger: #b42318;
      --ok: #0f766e;
    }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      background: var(--bg);
      color: var(--text);
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
      font-size: 14px;
    }}
    header {{
      padding: 16px 22px;
      background: var(--panel);
      border-bottom: 1px solid var(--line);
      display: flex;
      justify-content: space-between;
      align-items: center;
      gap: 12px;
    }}
    h1 {{ margin: 0; font-size: 20px; }}
    main {{
      display: grid;
      grid-template-columns: minmax(360px, 460px) minmax(520px, 1fr);
      gap: 14px;
      padding: 14px;
      height: calc(100vh - 65px);
    }}
    section {{
      background: var(--panel);
      border: 1px solid var(--line);
      border-radius: 8px;
      padding: 14px;
      margin-bottom: 12px;
    }}
    h2 {{ margin: 0 0 12px; font-size: 15px; }}
    label {{ display: block; font-weight: 650; margin: 10px 0 5px; }}
    input, select, textarea {{
      width: 100%;
      border: 1px solid var(--line);
      border-radius: 6px;
      padding: 9px 10px;
      background: var(--field);
      color: var(--text);
      font: inherit;
    }}
    textarea {{
      min-height: 210px;
      resize: vertical;
      line-height: 1.45;
    }}
    .grid2 {{ display: grid; grid-template-columns: 1fr 1fr; gap: 10px; }}
    .hint {{ color: var(--muted); font-size: 12px; line-height: 1.4; margin-top: 5px; }}
    .controls {{ display: flex; gap: 8px; flex-wrap: wrap; margin-top: 12px; }}
    button {{
      border: 0;
      border-radius: 6px;
      padding: 9px 12px;
      background: #e9eef5;
      color: var(--text);
      font: inherit;
      cursor: pointer;
    }}
    button.primary {{ background: var(--primary); color: #fff; }}
    button.danger {{ background: #fee4e2; color: var(--danger); }}
    button:disabled {{ opacity: 0.55; cursor: not-allowed; }}
    .statusrow {{
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 12px;
      flex-wrap: wrap;
    }}
    .statusactions {{
      display: flex;
      gap: 8px;
      flex-wrap: wrap;
    }}
    .right {{
      display: grid;
      grid-template-rows: auto minmax(360px, 1fr) auto;
      gap: 12px;
      min-height: 0;
    }}
    .statusbar {{
      background: var(--panel);
      border: 1px solid var(--line);
      border-radius: 8px;
      padding: 14px;
    }}
    .activity {{
      margin-top: 12px;
      padding: 10px 12px;
      border: 1px solid var(--line);
      border-radius: 8px;
      background: #f8fafc;
    }}
    .activity-main {{
      display: flex;
      align-items: center;
      gap: 9px;
      flex-wrap: wrap;
      font-weight: 650;
    }}
    .activity-dot {{
      width: 10px;
      height: 10px;
      border-radius: 50%;
      background: var(--muted);
      display: inline-block;
      flex: 0 0 auto;
    }}
    .activity-dot.running {{
      background: var(--primary);
      box-shadow: 0 0 0 0 rgba(37, 99, 235, 0.55);
      animation: pulse 1.3s infinite;
    }}
    .activity-dot.waiting {{
      background: #b54708;
      box-shadow: 0 0 0 0 rgba(181, 71, 8, 0.45);
      animation: pulse 1.3s infinite;
    }}
    .activity-dot.error {{ background: var(--danger); }}
    @keyframes pulse {{
      0% {{ box-shadow: 0 0 0 0 currentColor; }}
      70% {{ box-shadow: 0 0 0 8px rgba(255,255,255,0); }}
      100% {{ box-shadow: 0 0 0 0 rgba(255,255,255,0); }}
    }}
    .activity-file {{
      margin-top: 6px;
      color: #344054;
      word-break: break-word;
      line-height: 1.4;
    }}
    .activity-sub {{
      margin-top: 6px;
      color: var(--muted);
      font-size: 12px;
      line-height: 1.4;
    }}
    .retry-line {{
      margin-top: 7px;
      color: #b54708;
      font-weight: 650;
      font-size: 12px;
    }}
    .stats-grid {{
      display: grid;
      grid-template-columns: repeat(4, minmax(90px, 1fr));
      gap: 8px;
      margin-top: 10px;
    }}
    .stat-box {{
      border: 1px solid var(--line);
      border-radius: 6px;
      padding: 8px;
      background: #fff;
    }}
    .stat-label {{
      color: var(--muted);
      font-size: 11px;
      margin-bottom: 3px;
    }}
    .stat-value {{
      font-weight: 700;
      font-size: 15px;
    }}
    progress {{ width: 100%; height: 14px; margin-top: 8px; }}
    table {{
      width: 100%;
      border-collapse: collapse;
      background: var(--panel);
      border: 1px solid var(--line);
    }}
    th, td {{
      border-bottom: 1px solid var(--line);
      padding: 8px;
      text-align: left;
      vertical-align: top;
      font-size: 13px;
    }}
    th {{ background: #eef2f7; position: sticky; top: 0; }}
    .photo-cell {{ min-width: 170px; max-width: 190px; }}
    .row-thumb {{
      width: 150px;
      height: 104px;
      object-fit: contain;
      display: block;
      background: #f2f4f7;
      border: 1px solid var(--line);
      border-radius: 6px;
      margin-bottom: 7px;
    }}
    .filename {{ font-weight: 650; word-break: break-word; }}
    .photo-actions {{
      display: flex;
      flex-wrap: wrap;
      gap: 6px;
      margin-top: 8px;
    }}
    .photo-actions button {{
      min-width: 74px;
      padding: 6px 8px;
      font-size: 12px;
      line-height: 1.2;
    }}
    .zh-summary {{
      min-width: 180px;
      max-width: 260px;
      line-height: 1.45;
      color: #344054;
    }}
    .title {{ font-weight: 650; margin-bottom: 6px; }}
    .description, .keywords, .notes {{ line-height: 1.45; }}
    .keywords {{ min-width: 260px; max-width: 360px; }}
    .keyword-group {{
      margin-bottom: 10px;
      padding-bottom: 8px;
      border-bottom: 1px solid #eef2f7;
    }}
    .keyword-group:last-child {{ border-bottom: 0; margin-bottom: 0; padding-bottom: 0; }}
    .keyword-group-name {{ font-weight: 700; color: #344054; margin-bottom: 4px; }}
    .notes {{ max-width: 260px; }}
    .actions {{
      min-width: 280px;
      max-width: 360px;
    }}
    .copy-panel {{
      display: grid;
      gap: 8px;
      align-content: start;
    }}
    .copy-top-row {{
      display: grid;
      grid-template-columns: repeat(3, minmax(0, 1fr));
      gap: 6px;
    }}
    .copy-group {{
      display: grid;
      grid-template-columns: minmax(150px, 1fr) minmax(110px, 0.7fr);
      gap: 6px;
      align-items: stretch;
    }}
    .copy-divider {{
      height: 1px;
      background: var(--line);
      margin: 2px 0;
    }}
    .actions button {{
      width: 100%;
      min-width: 0;
      padding: 7px 9px;
      line-height: 1.25;
      overflow-wrap: anywhere;
      white-space: normal;
    }}
    .tablewrap, .logwrap {{
      min-height: 0;
      overflow: auto;
      background: var(--panel);
      border: 1px solid var(--line);
      border-radius: 8px;
    }}
    .logpanel {{
      min-height: 0;
      background: var(--panel);
      border: 1px solid var(--line);
      border-radius: 8px;
      overflow: hidden;
    }}
    .logpanel summary {{
      display: flex;
      align-items: center;
      justify-content: flex-start;
      gap: 12px;
      padding: 10px 12px;
      cursor: pointer;
      user-select: none;
      font-weight: 700;
      background: #f8fafc;
    }}
    .logpanel summary::-webkit-details-marker {{ display: none; }}
    .logpanel summary::before {{
      content: "▸";
      color: var(--muted);
      margin-right: 2px;
    }}
    .logpanel[open] summary::before {{ content: "▾"; }}
    .logpanel summary .hint {{
      margin-left: auto;
      font-weight: 500;
    }}
    .logpanel .logwrap {{
      border: 0;
      border-top: 1px solid var(--line);
      border-radius: 0;
      max-height: clamp(120px, 22vh, 220px);
    }}
    .log {{
      white-space: pre-wrap;
      padding: 12px;
      font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
      font-size: 12px;
      line-height: 1.45;
    }}
    .ok {{ color: var(--ok); font-weight: 650; }}
    .processing {{ color: #b54708; font-weight: 650; }}
    .pending {{ color: #175cd3; font-weight: 650; }}
    .superseded {{ color: #667085; font-weight: 650; }}
    .error {{ color: var(--danger); font-weight: 650; }}
    @media (max-width: 960px) {{
      main {{ grid-template-columns: 1fr; height: auto; }}
      .right {{ grid-template-rows: auto minmax(320px, auto) auto; }}
      .logpanel .logwrap {{ max-height: 240px; }}
      .actions {{ min-width: 0; max-width: none; }}
      .copy-group {{ grid-template-columns: 1fr 1fr; }}
    }}
  </style>
</head>
<body>
  <header>
    <h1>{APP_NAME}</h1>
    <div id="serverStatus" class="hint">本機瀏覽器介面</div>
  </header>
  <main>
    <form id="settingsForm">
      <section>
        <h2>1 照片來源</h2>
        <label>照片資料夾完整路徑</label>
        <div class="grid2">
          <input name="folder" value="{folder}" placeholder="/Users/你的帳號/Desktop/photos">
          <button type="button" id="chooseFolderBtn">選擇資料夾</button>
        </div>
        <div class="hint">不知道路徑時請按「選擇資料夾」；也可手動貼上完整路徑。</div>
      </section>

      <section>
        <h2>2 AI 模型</h2>
        <div class="grid2">
          <div>
            <label>Provider</label>
            <select name="provider">
              <option value="openai" {"selected" if provider == "openai" else ""}>openai</option>
              <option value="gemini" {"selected" if provider == "gemini" else ""}>gemini</option>
            </select>
          </div>
          <div>
            <label>Model 官方 API ID</label>
            <input name="model" value="{model}" list="modelSuggestions" placeholder="例如 gemini-3.1-flash-lite">
            <datalist id="modelSuggestions"></datalist>
          </div>
        </div>
        <div class="hint">下拉清單只放官方 API model ID；常見短名稱會在送出前自動轉成官方 ID。</div>
        <label>API Key</label>
        <input name="api_key" type="password" autocomplete="off" placeholder="只保存在這次執行，不寫入設定檔">
        <label><input name="use_cached_api_key" type="checkbox" style="width:auto" checked> API Key 空白時使用本機暫存</label>
        <label><input name="remember_api_key" type="checkbox" style="width:auto"> 記住這次輸入的 API Key 到本機</label>
        <div class="controls">
          <button type="button" id="keyStatusBtn">檢查暫存</button>
          <button type="button" id="clearKeyBtn">清除暫存 Key</button>
        </div>
        <div id="keyStatus" class="hint">API key 預設不存；勾選後才會暫存在本機。</div>
      </section>

      <section>
        <h2>3 圖庫需求 Prompt</h2>
        <label>Prompt 檔名</label>
        <input name="prompt_name" value="{prompt_name}" placeholder="例如 shutterstock_rules">
        <div class="controls">
          <button type="button" id="defaultPrompt">通用模板</button>
          <button type="button" id="savePromptBtn">儲存 Prompt</button>
          <select id="promptList" style="width:auto; min-width:180px"></select>
          <button type="button" id="loadPromptBtn">載入 Prompt</button>
          <button type="button" id="deletePromptBtn">刪除 Prompt</button>
        </div>
        <textarea name="prompt">{prompt}</textarea>
        <div class="hint">可在 Prompt 指定圖庫、語言、keyword 數量、分隔與排序規則；例如英文圖庫或日文圖庫。Prompt 會存到本機資料夾 ~/.stock_keyworder_prompts/。</div>
      </section>

      <section>
        <h2>4 執行</h2>
        <label><input name="watch" type="checkbox" style="width:auto" {watch_checked}> 監看資料夾</label>
        <div class="hint">預設最多處理 500 張照片；會保留 100 次 API 重試緩衝，仍有每日硬上限。</div>
        <div class="controls">
          <button class="primary" type="submit" id="startBtn">開始</button>
          <button class="danger" type="button" id="stopBtn">停止</button>
        </div>
      </section>
    </form>

    <div class="right">
      <div class="statusbar">
        <div class="statusrow">
          <div>
            <strong id="stateText">待命</strong>
            <span id="countText" class="hint"></span>
            <span id="pendingStatus" class="hint"></span>
          </div>
          <div class="statusactions">
            <button type="button" id="saveProgressBtn">儲存進度</button>
            <button type="button" id="loadProgressBtn">載入進度</button>
            <button type="button" id="resumeProgressBtn">繼續未完成</button>
          </div>
        </div>
        <progress id="progress" value="0" max="1"></progress>
        <div class="activity">
          <div class="activity-main">
            <span id="activityDot" class="activity-dot"></span>
            <span id="currentPhase">待命</span>
            <span id="currentElapsed" class="hint"></span>
          </div>
          <div id="currentFile" class="activity-file">尚未開始。</div>
          <div id="currentSub" class="activity-sub">開始後會顯示目前處理到哪張照片與 API 狀態。</div>
          <div id="retryLine" class="retry-line"></div>
          <div class="stats-grid">
            <div class="stat-box"><div class="stat-label">完成</div><div id="okStat" class="stat-value">0</div></div>
            <div class="stat-box"><div class="stat-label">錯誤</div><div id="errorStat" class="stat-value">0</div></div>
            <div class="stat-box"><div class="stat-label">剩餘</div><div id="remainingStat" class="stat-value">0</div></div>
            <div class="stat-box"><div class="stat-label">進度</div><div id="percentStat" class="stat-value">0%</div></div>
          </div>
        </div>
      </div>
      <div class="tablewrap">
        <table>
          <thead><tr><th>照片 / 檔名</th><th>狀態</th><th>中文說明</th><th>Title / Description</th><th>Keywords</th><th>Notes</th><th>複製</th></tr></thead>
          <tbody id="results"></tbody>
        </table>
      </div>
      <details id="logPanel" class="logpanel">
        <summary><span>監測紀錄</span><span id="logSummary" class="hint">0 筆</span></summary>
        <div class="logwrap"><div id="log" class="log"></div></div>
      </details>
    </div>
  </main>
  <script>
    const defaultPrompt = {json.dumps(DEFAULT_PROMPT, ensure_ascii=False)};

    const form = document.getElementById('settingsForm');
    const stateText = document.getElementById('stateText');
    const countText = document.getElementById('countText');
    const progress = document.getElementById('progress');
    const activityDot = document.getElementById('activityDot');
    const currentPhase = document.getElementById('currentPhase');
    const currentElapsed = document.getElementById('currentElapsed');
    const currentFile = document.getElementById('currentFile');
    const currentSub = document.getElementById('currentSub');
    const retryLine = document.getElementById('retryLine');
    const okStat = document.getElementById('okStat');
    const errorStat = document.getElementById('errorStat');
    const remainingStat = document.getElementById('remainingStat');
    const percentStat = document.getElementById('percentStat');
    const results = document.getElementById('results');
    const log = document.getElementById('log');
    const logPanel = document.getElementById('logPanel');
    const logSummary = document.getElementById('logSummary');
    const promptList = document.getElementById('promptList');
    const keyStatus = document.getElementById('keyStatus');
    const pendingStatus = document.getElementById('pendingStatus');
    const internalDefaults = {{
      max_images: {MAX_IMAGES},
      max_side: 1600,
      max_file_mb: {DEFAULT_MAX_FILE_MB},
      daily_limit: {DEFAULT_DAILY_API_LIMIT},
      retry_count: {DEFAULT_RETRY_COUNT},
      reuse_similar_images: {str(DEFAULT_REUSE_SIMILAR_IMAGES).lower()},
      similar_threshold: {DEFAULT_SIMILARITY_THRESHOLD}
    }};
    const providerDefaults = {json.dumps(PROVIDER_DEFAULT_MODELS, ensure_ascii=False)};
    const providerModels = {json.dumps(PROVIDER_MODEL_SUGGESTIONS, ensure_ascii=False)};
    const knownProviders = Object.keys(providerDefaults);
    let lastStatus = null;
    let serverOffsetSeconds = 0;

    document.getElementById('chooseFolderBtn').onclick = async () => {{
      const button = document.getElementById('chooseFolderBtn');
      const oldText = button.textContent;
      button.disabled = true;
      button.textContent = '選擇中...';
      try {{
        const data = await postJson('/api/select-folder', {{}});
        if (data.folder) {{
          form.folder.value = data.folder;
        }}
      }} catch (error) {{
        alert(error.message);
      }} finally {{
        button.disabled = false;
        button.textContent = oldText;
      }}
    }};

    document.getElementById('defaultPrompt').onclick = () => form.prompt.value = defaultPrompt;
    document.getElementById('savePromptBtn').onclick = async () => {{
      try {{
        await postJson('/api/save-prompt', {{name: form.prompt_name.value, prompt: form.prompt.value}});
        await loadPromptList();
        alert('Prompt 已儲存');
      }} catch (error) {{
        alert(error.message);
      }}
    }};
    document.getElementById('loadPromptBtn').onclick = async () => {{
      try {{
        const name = promptList.value || form.prompt_name.value;
        const data = await postJson('/api/load-prompt', {{name}});
        form.prompt.value = data.prompt || '';
        form.prompt_name.value = data.name || name;
      }} catch (error) {{
        alert(error.message);
      }}
    }};
    document.getElementById('deletePromptBtn').onclick = async () => {{
      try {{
        const name = promptList.value || form.prompt_name.value;
        if (!name) throw new Error('請先選擇或輸入 Prompt 名稱');
        if (!confirm('確定刪除 Prompt：' + name + '？')) return;
        await postJson('/api/delete-prompt', {{name}});
        form.prompt_name.value = '';
        await loadPromptList();
        alert('Prompt 已刪除');
      }} catch (error) {{
        alert(error.message);
      }}
    }};
    document.getElementById('keyStatusBtn').onclick = () => refreshKeyStatus();
    document.getElementById('saveProgressBtn').onclick = async () => {{
      try {{
        const data = await postJson('/api/save-progress', {{}});
        await refreshPendingStatus();
        alert('已儲存 ' + data.count + ' 筆進度');
      }} catch (error) {{
        alert(error.message);
      }}
    }};
    document.getElementById('loadProgressBtn').onclick = async () => {{
      try {{
        if (!confirm('載入進度會覆蓋目前畫面清單，確定繼續？')) return;
        const data = await postJson('/api/load-progress', {{}});
        await refresh();
        await refreshPendingStatus();
        alert('已載入 ' + data.count + ' 筆進度');
      }} catch (error) {{
        alert(error.message);
      }}
    }};
    document.getElementById('resumeProgressBtn').onclick = async () => {{
      try {{
        await postJson('/api/resume', formPayload());
      }} catch (error) {{
        alert(error.message);
      }}
    }};
    document.getElementById('clearKeyBtn').onclick = async () => {{
      try {{
        await postJson('/api/clear-key', {{provider: form.provider.value}});
        await refreshKeyStatus();
        alert('已清除本機暫存 API key');
      }} catch (error) {{
        alert(error.message);
      }}
    }};
    form.provider.addEventListener('change', () => {{
      syncModelForProvider();
      refreshModelSuggestions();
      refreshKeyStatus();
    }});

    function detectModelProvider(model) {{
      const value = String(model || '').trim().toLowerCase();
      if (value.startsWith('gemini-')) return 'gemini';
      if (/^(gpt-|o1|o3|o4|o5|chatgpt-)/.test(value)) return 'openai';
      return '';
    }}

    function syncModelForProvider() {{
      const provider = form.provider.value;
      const detected = detectModelProvider(form.model.value);
      if (!form.model.value.trim() || (detected && detected !== provider)) {{
        form.model.value = providerDefaults[provider] || '';
      }}
    }}

    function refreshModelSuggestions() {{
      const list = document.getElementById('modelSuggestions');
      const models = providerModels[form.provider.value] || [];
      list.innerHTML = models.map(model => `<option value="${{esc(model)}}"></option>`).join('');
    }}

    function formPayload() {{
      const data = Object.fromEntries(new FormData(form).entries());
      data.watch = form.watch.checked;
      data.use_cached_api_key = form.use_cached_api_key.checked;
      data.remember_api_key = form.remember_api_key.checked;
      Object.assign(data, internalDefaults);
      return data;
    }}

    async function postJson(url, data) {{
      const response = await fetch(url, {{
        method: 'POST',
        headers: {{'Content-Type': 'application/json'}},
        body: JSON.stringify(data || {{}})
      }});
      const body = await response.json();
      if (!response.ok) throw new Error(body.error || response.statusText);
      return body;
    }}

    form.onsubmit = async (event) => {{
      event.preventDefault();
      try {{
        await postJson('/api/start', formPayload());
      }} catch (error) {{
        alert(error.message);
      }}
    }};

    document.getElementById('stopBtn').onclick = () => postJson('/api/stop').catch(error => alert(error.message));
    document.addEventListener('click', async (event) => {{
      const copyButton = event.target.closest('button[data-copy]');
      if (copyButton) {{
        await navigator.clipboard.writeText(copyButton.getAttribute('data-copy') || '');
        const oldText = copyButton.textContent;
        copyButton.textContent = '已複製';
        setTimeout(() => copyButton.textContent = oldText, 800);
        return;
      }}

      const reanalyzeButton = event.target.closest('button[data-reanalyze-index]');
      if (reanalyzeButton) {{
        const filename = reanalyzeButton.getAttribute('data-filename') || '這張照片';
        const correction = prompt(
          '請輸入正確資訊。若 AI 正在工作，這張會先排到最後面，等目前任務完成後再重新辨識。\\n例如：這張是鹹蛋苦瓜，不是炒高麗菜。',
          ''
        );
        if (!correction || !correction.trim()) return;
        await postJson('/api/reanalyze-result', {{
          ...formPayload(),
          index: Number(reanalyzeButton.getAttribute('data-reanalyze-index') || 0),
          correction: correction.trim()
        }});
        await refresh();
        alert('已排入修正重辨：' + filename);
        return;
      }}

      const deleteButton = event.target.closest('button[data-delete-index]');
      if (!deleteButton) return;
      const filename = deleteButton.getAttribute('data-filename') || '此筆資料';
      if (!confirm('只會從清單移除，不會刪除原始照片。確定刪除：' + filename + '？')) return;
      await postJson('/api/delete-result', {{
        index: Number(deleteButton.getAttribute('data-delete-index') || 0)
      }});
      await refresh();
      await refreshPendingStatus();
    }});

    function esc(value) {{
      return String(value ?? '').replace(/[&<>"']/g, ch => ({{
        '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'
      }}[ch]));
    }}

    function thumbnailSrc(item) {{
      const version = [item.filename || '', item.source_path || '', item.status || ''].join('|');
      return '/api/thumbnail?index=' + encodeURIComponent(item.index || 0)
        + '&v=' + encodeURIComponent(version);
    }}

    function localizedStatus(item) {{
      if (item.status === 'ok') return '完成';
      if (item.status === 'processing') return '修正中';
      if (item.status === 'pending_reanalyze') return '待修正';
      if (item.status === 'superseded') return '待移除';
      return '錯誤';
    }}

    function statusClass(item) {{
      if (item.status === 'ok') return 'ok';
      if (item.status === 'processing') return 'processing';
      if (item.status === 'pending_reanalyze') return 'pending';
      if (item.status === 'superseded') return 'superseded';
      return 'error';
    }}

    function zhSummary(item) {{
      if (item.zh_summary) return item.zh_summary;
      const message = String(item.notes || item.error || '');
      if (/503|high demand|滿載|UNAVAILABLE/i.test(message)) {{
        return '模型目前滿載，這張尚未完成；稍後按「繼續未完成」即可重新嘗試。';
      }}
      if (item.status !== 'ok') return '這張尚未完成，請查看錯誤原因後稍後續跑。';
      return item.title ? '照片內容請參考英文標題：' + item.title : '尚未產生中文說明。';
    }}

    function itemKeywordGroups(item) {{
      const rawGroups = Array.isArray(item.keyword_groups) ? item.keyword_groups : [];
      const groups = rawGroups.map((group, index) => {{
        const keywords = Array.isArray(group.keywords) ? group.keywords : [];
        return {{
          name: String(group.name || ('Keywords ' + (index + 1))),
          language: String(group.language || ''),
          keywords,
          notes: String(group.notes || ''),
          copy_line: String(group.copy_line || '')
        }};
      }}).filter(group => group.keywords.length > 0);
      if (!groups.length && Array.isArray(item.keywords) && item.keywords.length) {{
        groups.push({{
          name: 'Keywords',
          language: '',
          keywords: item.keywords,
          notes: '',
          copy_line: item.copy_line || ''
        }});
      }}
      return groups;
    }}

    function groupKeywordText(group) {{
      return (group.keywords || []).join(', ');
    }}

    function groupCopyLine(item, group) {{
      return group.copy_line || [item.title || '', item.description || '', groupKeywordText(group)].join('\\t');
    }}

    function renderKeywordGroups(item) {{
      const groups = itemKeywordGroups(item);
      if (!groups.length) return '';
      return groups.map(group => {{
        const language = group.language ? ' · ' + group.language : '';
        const notes = group.notes ? '<div class="hint">' + esc(group.notes) + '</div>' : '';
        return `
          <div class="keyword-group">
            <div class="keyword-group-name">${{esc(group.name)}} · ${{group.keywords.length}} 個 keywords${{esc(language)}}</div>
            <div>${{esc(groupKeywordText(group))}}</div>
            ${{notes}}
          </div>
        `;
      }}).join('');
    }}

    function renderPhotoActions(item) {{
      if (item.status === 'pending_reanalyze' || item.status === 'processing') {{
        return '';
      }}
      const buttons = [];
      if (item.status !== 'superseded') {{
        buttons.push(`<button type="button" data-reanalyze-index="${{esc(item.index)}}" data-filename="${{esc(item.filename)}}">修正重辨</button>`);
      }}
      buttons.push(`<button type="button" data-delete-index="${{esc(item.index)}}" data-filename="${{esc(item.filename)}}">刪除</button>`);
      return `<div class="photo-actions">${{buttons.join('')}}</div>`;
    }}

    function renderCopyButtons(item) {{
      if (['pending_reanalyze', 'processing', 'superseded'].includes(item.status)) {{
        return '';
      }}
      const groups = itemKeywordGroups(item);
      const title = item.title || '';
      const description = item.description || '';
      const topButtons = [
        `<button type="button" data-copy="${{esc(title)}}">標題</button>`,
        `<button type="button" data-copy="${{esc(description)}}">描述</button>`,
        `<button type="button" data-copy="${{esc([title, description].join('\\t'))}}">標題+描述</button>`
      ];
      const groupRows = [];
      groups.forEach(group => {{
        const label = group.name || 'Keywords';
        groupRows.push(`
          <div class="copy-group">
            <button type="button" data-copy="${{esc(groupKeywordText(group))}}">${{esc(label)}}關鍵字(${{group.keywords.length}})</button>
            <button type="button" data-copy="${{esc(groupCopyLine(item, group))}}">${{esc(label)}}整列</button>
          </div>
        `);
      }});
      const mainLine = item.copy_line || [title, description, (item.keywords || []).join(', ')].join('\\t');
      return `
        <div class="copy-panel">
          <div class="copy-top-row">${{topButtons.join('')}}</div>
          <div class="copy-divider"></div>
          ${{groupRows.join('')}}
          <button type="button" data-copy="${{esc(mainLine)}}">主整列</button>
        </div>
      `;
    }}

    function formatDuration(seconds) {{
      const total = Math.max(0, Math.floor(Number(seconds) || 0));
      const minutes = Math.floor(total / 60);
      const secs = total % 60;
      if (minutes <= 0) return secs + ' 秒';
      return minutes + ' 分 ' + String(secs).padStart(2, '0') + ' 秒';
    }}

    function browserServerNow() {{
      return Date.now() / 1000 + serverOffsetSeconds;
    }}

    function renderActivity(status) {{
      const current = status.current || {{}};
      const now = browserServerNow();
      const total = Number(status.total || current.total || 0);
      const done = Number(status.done || 0);
      const percent = total > 0 ? Math.round((done / total) * 100) : 0;
      const phase = current.phase || status.state || '待命';
      const isWatchMode = status.mode === 'watch';
      const isWatchIdle = isWatchMode && status.running && !current.filename && /監看中/.test(phase);
      const retryRemaining = current.retry_until ? Math.max(0, Math.ceil(Number(current.retry_until) - now)) : 0;
      const startedAt = Number(current.started_at || current.updated_at || now);
      const elapsed = Math.max(0, now - startedAt);

      currentPhase.textContent = phase;
      currentElapsed.textContent = status.running
        ? (isWatchIdle ? '已監看 ' + formatDuration(elapsed) : '已等待 ' + formatDuration(elapsed))
        : '';
      if (current.filename) {{
        currentFile.textContent = '第 ' + (current.index || '-') + ' / ' + (current.total || total || '-') + ' 張：' + current.filename;
      }} else if (isWatchIdle) {{
        currentFile.textContent = '目前沒有照片正在送 API。';
      }} else {{
        currentFile.textContent = status.running ? '正在準備工作。' : '尚未開始。';
      }}

      const attempts = current.max_attempts
        ? 'API 嘗試 ' + (current.attempt || 1) + ' / ' + current.max_attempts
        : '';
      currentSub.textContent = attempts || (
        isWatchIdle
          ? '目前批次已處理完成，正在等待新照片。若不需要繼續監看，按「停止」。'
          : (status.running ? '程式仍在執行，請等候目前 API 回應。' : '開始後會顯示目前處理到哪張照片與 API 狀態。')
      );

      retryLine.textContent = retryRemaining > 0
        ? '模型忙碌或暫時限流，' + retryRemaining + ' 秒後自動重試。'
        : '';

      activityDot.className = 'activity-dot';
      if (status.state === '錯誤') {{
        activityDot.classList.add('error');
      }} else if (retryRemaining > 0 || /等待/.test(phase)) {{
        activityDot.classList.add('waiting');
      }} else if (status.running) {{
        activityDot.classList.add('running');
      }}

      okStat.textContent = status.ok_count || 0;
      errorStat.textContent = status.error_count || 0;
      remainingStat.textContent = status.remaining || 0;
      percentStat.textContent = percent + '%';
    }}

    async function refresh() {{
      const status = await fetch('/api/status').then(r => r.json());
      lastStatus = status;
      if (status.server_time) {{
        serverOffsetSeconds = Number(status.server_time) - Date.now() / 1000;
      }}
      stateText.textContent = status.state || '待命';
      countText.textContent = status.mode === 'watch'
        ? ' 已處理 ' + (status.done || 0) + ' / 上限 ' + (status.total || 0)
        : ' ' + (status.done || 0) + ' / ' + (status.total || 0);
      progress.max = Math.max(status.total || 1, 1);
      progress.value = status.done || 0;
      renderActivity(status);
      const logs = status.logs || [];
      log.textContent = logs.join('\\n');
      logSummary.textContent = logs.length ? logs.length + ' 筆' : '0 筆';
      if (logPanel.open) {{
        log.parentElement.scrollTop = log.parentElement.scrollHeight;
      }}
      results.innerHTML = (status.results || []).map(item => {{
        return `
          <tr>
            <td class="photo-cell">
              <img class="row-thumb" src="${{esc(thumbnailSrc(item))}}" alt="">
              <div class="filename">${{esc(item.filename)}}</div>
              <div class="hint">#${{esc(item.index || '')}}</div>
              ${{renderPhotoActions(item)}}
            </td>
            <td class="${{statusClass(item)}}">${{esc(localizedStatus(item))}}</td>
            <td class="zh-summary">${{esc(zhSummary(item))}}</td>
            <td>
              <div class="title">${{esc(item.title)}}</div>
              <div class="description">${{esc(item.description)}}</div>
            </td>
            <td class="keywords">${{renderKeywordGroups(item)}}</td>
            <td class="notes">${{esc(item.notes || item.error || '')}}</td>
            <td class="actions">
              ${{renderCopyButtons(item)}}
            </td>
          </tr>
        `;
      }}).join('');
    }}
    async function loadPromptList() {{
      const data = await fetch('/api/prompts').then(r => r.json());
      promptList.innerHTML = (data.prompts || []).map(name => `<option value="${{esc(name)}}">${{esc(name)}}</option>`).join('');
    }}
    async function refreshKeyStatus() {{
      const provider = encodeURIComponent(form.provider.value);
      const data = await fetch('/api/key-status?provider=' + provider).then(r => r.json());
      keyStatus.textContent = data.has_key
        ? '已暫存 ' + data.provider + ' API key：' + data.storage
        : '尚未暫存 ' + data.provider + ' API key';
    }}
    async function refreshPendingStatus() {{
      const data = await fetch('/api/pending-status').then(r => r.json());
      pendingStatus.textContent = data.exists
        ? ' · 已存 ' + data.count + ' 筆進度'
        : '';
    }}
    setInterval(refresh, 900);
    setInterval(() => {{
      if (lastStatus) renderActivity(lastStatus);
    }}, 500);
    syncModelForProvider();
    refreshModelSuggestions();
    loadPromptList();
    refreshKeyStatus();
    refreshPendingStatus();
    refresh();
  </script>
</body>
</html>"""


def run_web_gui(port: int = 8765) -> None:
    settings = load_saved_settings()
    app_state: dict[str, Any] = {
        "state": "待命",
        "done": 0,
        "total": 0,
        "logs": [],
        "results": [],
        "completed_sources": set(),
        "thumbnail_cache": {},
        "current": current_progress_payload("待命"),
        "manifest": {},
        "mode": "",
        "reanalyze_queue": [],
        "next_reanalyze_id": 1,
        "running": False,
        "stop_event": None,
        "worker": None,
    }
    state_lock = threading.Lock()

    def add_log(message: str) -> None:
        with state_lock:
            logs = app_state["logs"]
            logs.append(f"{dt.datetime.now().strftime('%H:%M:%S')}  {message}")
            del logs[:-300]

    def snapshot() -> dict[str, Any]:
        with state_lock:
            results_copy = list(app_state["results"])
            ok_count = len([result for result in results_copy if result.status == "ok"])
            error_count = len([result for result in results_copy if result.status == "error"])
            total = int(app_state["total"])
            done = int(app_state["done"])
            return {
                "state": app_state["state"],
                "done": done,
                "total": total,
                "ok_count": ok_count,
                "error_count": error_count,
                "remaining": max(total - done, 0),
                "logs": list(app_state["logs"]),
                "results": [asdict(result) for result in results_copy],
                "running": app_state["running"],
                "mode": app_state.get("mode", ""),
                "current": dict(app_state["current"]),
                "server_time": time.time(),
                "manifest": dict(app_state["manifest"]),
            }

    def thumbnail_for_index(index: int) -> tuple[str, bytes]:
        with state_lock:
            result = next(
                (item for item in app_state["results"] if int(item.index) == index),
                None,
            )
            cache = app_state["thumbnail_cache"]
        if result is None:
            raise FileNotFoundError("找不到縮圖。")

        source = Path(result.source_path)
        try:
            stat = source.stat()
            cache_key = f"{source_key_for_result(result)}:{stat.st_mtime_ns}:{stat.st_size}"
        except Exception:
            cache_key = source_key_for_result(result)

        with state_lock:
            cached = cache.get(cache_key)
        if cached:
            return cached

        thumbnail = create_thumbnail_response(source)
        with state_lock:
            cache[cache_key] = thumbnail
            if len(cache) > MAX_IMAGES:
                for key in list(cache.keys())[: len(cache) - MAX_IMAGES]:
                    cache.pop(key, None)
        return thumbnail

    def set_state(**updates: Any) -> None:
        with state_lock:
            app_state.update(updates)

    def set_current(payload: dict[str, Any]) -> None:
        current = dict(payload)
        current.setdefault("updated_at", time.time())
        current.setdefault("started_at", current["updated_at"])
        with state_lock:
            app_state["current"] = current

    def config_from_payload(payload: dict[str, Any]) -> tuple[RunConfig, bool]:
        provider = str(payload.get("provider", "openai")).strip().lower()
        model = normalize_model_for_provider(
            provider,
            str(payload.get("model") or PROVIDER_DEFAULT_MODELS.get(provider, "")),
        )
        api_key = str(payload.get("api_key", "")).strip()
        if not api_key and bool(payload.get("use_cached_api_key", True)):
            api_key = load_cached_api_key(provider)
        if api_key and bool(payload.get("remember_api_key", False)):
            storage = save_cached_api_key(provider, api_key)
            add_log(f"API key 已暫存在本機：{storage}")
        return (
            RunConfig(
                folder=Path(str(payload.get("folder", "")).strip()).expanduser(),
                provider=provider,
                model=model,
                api_key=api_key,
                prompt=str(payload.get("prompt", "")).strip(),
                output_dir=None,
                save_outputs=False,
                max_images=int(payload.get("max_images", MAX_IMAGES)),
                max_side=int(payload.get("max_side", 1600)),
                max_file_mb=int(payload.get("max_file_mb", DEFAULT_MAX_FILE_MB)),
                retry_count=int(payload.get("retry_count", DEFAULT_RETRY_COUNT)),
                daily_limit=int(payload.get("daily_limit", DEFAULT_DAILY_API_LIMIT)),
                reuse_similar_images=bool(payload.get("reuse_similar_images", DEFAULT_REUSE_SIMILAR_IMAGES)),
                similar_threshold=int(payload.get("similar_threshold", DEFAULT_SIMILARITY_THRESHOLD)),
            ),
            bool(payload.get("watch", False)),
        )

    def run_job(config: RunConfig, watch_mode: bool, stop_event: threading.Event) -> None:
        def progress(kind: str, payload: Any) -> None:
            if kind == "scan":
                mode = str(payload.get("mode", "batch"))
                total = int(payload["total"])
                phase = "監看中" if mode == "watch" else "掃描完成，準備開始"
                set_state(total=total, done=0, state=("監看中" if mode == "watch" else "執行中"), mode=mode)
                set_current(current_progress_payload(phase, total=total))
                add_log(f"監看上限 {total} 張；放入新照片後會自動分析。" if mode == "watch" else f"找到 {total} 張")
            elif kind == "log":
                add_log(redact_sensitive(payload, [config.api_key]))
            elif kind == "current":
                set_current(payload)
            elif kind == "progress":
                set_state(done=int(payload["done"]), total=int(payload["total"]))
            elif kind == "result":
                with state_lock:
                    app_state["results"].append(payload)
                    if is_completed_result(payload):
                        source_token = completed_source_token_for_result(payload)
                        if source_token:
                            app_state["completed_sources"].add(source_token)
            elif kind == "saved":
                if watch_mode:
                    total_limit = config.max_images
                    count = int(payload.get("count", 0) or 0)
                    set_state(manifest=payload, state="監看中", done=count, total=total_limit, mode="watch")
                    set_current(current_progress_payload("監看中", total=total_limit))
                    add_log(f"目前批次完成，已處理 {count} 張；繼續監看新照片。")
                    if has_reanalyze_queue():
                        drain_reanalyze_queue(stop_event)
                        set_state(state="監看中", mode="watch", running=True)
                        set_current(current_progress_payload("監看中", total=total_limit))
                else:
                    set_state(manifest=payload, state="完成")
                    set_current(current_progress_payload("完成", total=int(payload.get("count", 0) or 0)))
                    add_log("完成，結果已顯示在右側表格。")
            elif kind == "watch_idle":
                if watch_mode and has_reanalyze_queue():
                    drain_reanalyze_queue(stop_event)
                    set_state(state="監看中", mode="watch", running=True)
                    set_current(current_progress_payload("監看中", total=config.max_images))
            elif kind == "done":
                final_state = "已停止" if watch_mode and stop_event.is_set() else "完成"
                set_state(manifest=payload, state=final_state)
                total_count = int(payload.get("count", 0) or 0)
                if not total_count:
                    with state_lock:
                        total_count = int(app_state["total"])
                set_current(current_progress_payload(final_state, total=total_count))
                if payload.get("html"):
                    add_log(f"報表：{payload.get('html', '')}")
                else:
                    add_log("監看已停止。" if final_state == "已停止" else "完成，結果已顯示在右側表格。")

        try:
            if watch_mode:
                manifest = watch_folder(config, progress=progress, stop_event=stop_event)
            else:
                manifest = process_folder(config, progress=progress, stop_event=stop_event)
            set_state(manifest=manifest, state="完成")
        except Exception as exc:
            set_state(state="錯誤")
            set_current(current_progress_payload("錯誤"))
            add_log(f"錯誤：{redact_sensitive(exc, [config.api_key])}")
        finally:
            if has_reanalyze_queue():
                drain_reanalyze_queue(stop_event)
            set_state(running=False)

    def run_resume_job(config: RunConfig, stop_event: threading.Event) -> None:
        def progress(kind: str, payload: Any) -> None:
            if kind == "log":
                add_log(redact_sensitive(payload, [config.api_key]))
            elif kind == "current":
                set_current(payload)
            elif kind == "progress":
                set_state(done=int(payload["done"]), total=int(payload["total"]))
            elif kind == "result":
                with state_lock:
                    app_state["results"].append(payload)
                    if is_completed_result(payload):
                        source_token = completed_source_token_for_result(payload)
                        if source_token:
                            app_state["completed_sources"].add(source_token)

        try:
            api_key = prepare_run(config)
            with state_lock:
                all_existing_results = reindex_results(list(app_state["results"]))
                stale_completed_count = len(
                    [
                        result
                        for result in all_existing_results
                        if is_completed_result(result) and not result_matches_config(result, config)
                    ]
                )
                existing_results = reindex_results(
                    [
                        result
                        for result in all_existing_results
                        if is_completed_result(result) and result_matches_config(result, config)
                    ]
                )
                retryable_error_count = len(all_existing_results) - len(existing_results) - stale_completed_count
                completed_sources = set(app_state["completed_sources"])
                app_state["results"] = existing_results
                app_state["completed_sources"] = completed_sources | completed_sources_from_results(existing_results, config)

            remaining_images = discover_remaining_images(config, existing_results, completed_sources)
            total_count = len(existing_results) + len(remaining_images)
            ensure_daily_limit(config, preflight_api_call_count(config, remaining_images))
            set_state(total=total_count, done=len(existing_results), state="執行中")
            set_current(
                current_progress_payload(
                    "準備續跑",
                    index=len(existing_results),
                    total=total_count,
                )
            )
            if retryable_error_count:
                add_log(f"續跑：移除 {retryable_error_count} 筆可重試錯誤，會重新分析。")
            if stale_completed_count:
                add_log(
                    f"續跑：偵測到 prompt、provider 或 model 已變更，"
                    f"移除 {stale_completed_count} 筆舊完成資料並重新分析。"
                )
            add_log(f"續跑：已保留 {len(existing_results)} 筆完成資料，剩餘 {len(remaining_images)} 張未完成照片。")

            if remaining_images:
                analyze_images(
                    config,
                    remaining_images,
                    api_key,
                    progress=progress,
                    stop_event=stop_event,
                    start_index=len(existing_results) + 1,
                    completed_before=len(existing_results),
                    total_count=total_count,
                    reuse_candidates=existing_results,
                )

            with state_lock:
                final_results = reindex_results(list(app_state["results"]))
                app_state["results"] = final_results
            manifest = build_result_manifest(final_results, config)
            set_state(manifest=manifest, state="完成")
            set_current(current_progress_payload("完成", total=len(final_results)))
            add_log("續跑完成，結果已顯示在右側表格。")
        except Exception as exc:
            set_state(state="錯誤")
            set_current(current_progress_payload("錯誤"))
            add_log(f"錯誤：{redact_sensitive(exc, [config.api_key])}")
        finally:
            if has_reanalyze_queue():
                drain_reanalyze_queue(stop_event)
            set_state(running=False)

    def replace_result_by_index(target_index: int, replacement: ImageResult) -> bool:
        with state_lock:
            current_results = list(app_state["results"])
            for position, existing in enumerate(current_results):
                if int(existing.index) == target_index:
                    replacement.index = existing.index
                    current_results[position] = replacement
                    app_state["results"] = reindex_results(current_results)
                    return True
        return False

    def run_reanalyze_job(
        config: RunConfig,
        target_index: int,
        correction: str,
        stop_event: threading.Event,
        original_index: int = 0,
        queue_id: str = "",
        manage_running: bool = True,
    ) -> None:
        base_prompt = config.prompt
        api_key = ""
        metadata_signature = ""
        target: Optional[ImageResult] = None
        queue_marker = f"修正重辨 {queue_id}" if queue_id else ""
        try:
            api_key = prepare_run(config)
            metadata_signature = metadata_signature_for_config(config)
            with state_lock:
                for result in app_state["results"]:
                    if queue_marker and queue_marker in (result.notes or "") and result.status in {
                        "pending_reanalyze",
                        "processing",
                    }:
                        target = result
                        break
                    if not queue_marker and int(result.index) == target_index:
                        target = result
                        break
                if target is None:
                    raise ValueError("找不到要修正重辨的照片。")
                remove_completed_source_for_result(app_state["completed_sources"], target)

            image_path = Path(target.source_path)
            if not image_path.exists():
                raise FileNotFoundError(f"找不到原始照片：{target.source_path}")
            if not is_within_file_limit(image_path, config.max_file_mb):
                raise ValueError(file_limit_error(image_path, config.max_file_mb))

            placeholder = ImageResult(
                index=target.index,
                filename=target.filename,
                source_path=target.source_path,
                status="processing",
                provider=config.provider,
                model=config.model,
                title=target.title,
                description=target.description,
                zh_summary="正在依修正資訊重新辨識。",
                keywords=list(target.keywords or []),
                keyword_groups=[dict(group) for group in (target.keyword_groups or [])],
                categories=list(target.categories or []),
                notes=f"{queue_marker + '：' if queue_marker else ''}修正資訊：{correction[:200]}",
                copy_line=target.copy_line,
                prompt_signature=metadata_signature,
            )
            replace_result_by_index(target.index, placeholder)

            corrected_prompt = prompt_with_user_correction(base_prompt, image_path.name, correction)
            config.prompt = corrected_prompt
            total = int(app_state.get("total") or len(app_state.get("results") or []))
            set_state(state="修正重辨中", mode="reanalyze")
            add_log(f"[修正重辨] {image_path.name}：{correction}")

            raw_metadata: Optional[dict[str, Any]] = None
            last_error: Optional[Exception] = None
            for attempt in range(config.retry_count + 1):
                if stop_event.is_set():
                    raise RuntimeError("修正重辨已停止。")
                try:
                    ensure_daily_limit(config, 1)
                    record_api_attempt(config)
                    set_current(
                        current_progress_payload(
                            "修正重辨中" if attempt == 0 else "修正重辨重新送出",
                            image_path.name,
                            target_index,
                            total,
                            attempt=attempt + 1,
                            max_attempts=config.retry_count + 1,
                        )
                    )
                    raw_metadata = analyze_one_image(
                        config,
                        image_path,
                        api_key,
                        strict_json_retry=isinstance(last_error, ModelOutputFormatError),
                    )
                    break
                except UsageLimitError:
                    raise
                except Exception as exc:
                    last_error = exc
                    if attempt < config.retry_count:
                        wait_seconds = retry_wait_seconds(exc, attempt)
                        retry_until = time.time() + wait_seconds
                        set_current(
                            current_progress_payload(
                                "修正重辨等待重試",
                                image_path.name,
                                target_index,
                                total,
                                attempt=attempt + 1,
                                max_attempts=config.retry_count + 1,
                                retry_until=retry_until,
                            )
                        )
                        add_log(
                            f"[修正重辨] 重試 {attempt + 1}/{config.retry_count}："
                            f"{redact_sensitive(exc, [api_key, config.api_key])}，等待 {wait_seconds} 秒"
                        )
                        wait_for_retry(wait_seconds, stop_event)

            if raw_metadata is None:
                raise last_error or RuntimeError("修正重辨失敗。")

            normalized = normalize_metadata(raw_metadata)
            corrected = ImageResult(
                index=target.index,
                filename=target.filename,
                source_path=target.source_path,
                status="ok",
                provider=config.provider,
                model=config.model,
                title=normalized["title"],
                description=normalized["description"],
                zh_summary=normalized["zh_summary"],
                keywords=normalized["keywords"],
                keyword_groups=normalized["keyword_groups"],
                categories=normalized["categories"],
                notes=normalized["notes"],
                copy_line=normalized["copy_line"],
                prompt_signature=metadata_signature,
            )
            with state_lock:
                current_results = list(app_state["results"])
                replaced_results: list[ImageResult] = []
                for result in current_results:
                    if int(result.index) == int(target.index):
                        replaced_results.append(corrected)
                    elif queue_marker and queue_marker in (result.notes or "") and result.status == "superseded":
                        continue
                    elif original_index and int(result.index) == original_index and int(result.index) != int(target.index):
                        continue
                    else:
                        replaced_results.append(result)
                app_state["results"] = reindex_results(replaced_results)
                source_token = completed_source_token_for_result(corrected)
                if source_token:
                    app_state["completed_sources"].add(source_token)
                results_copy = list(app_state["results"])
                completed_sources = set(app_state["completed_sources"])
            save_pending_results(results_copy, completed_sources)
            set_current(current_progress_payload("修正重辨完成", image_path.name, target_index, total))
            set_state(state="完成")
            add_log(f"[修正重辨] 完成：{image_path.name}")
        except Exception as exc:
            message = redact_sensitive(exc, [api_key, config.api_key])
            if target is not None:
                failed = ImageResult(
                    index=target.index,
                    filename=target.filename,
                    source_path=target.source_path,
                    status="error",
                    provider=config.provider,
                    model=config.model,
                    zh_summary="修正重辨失敗，請查看錯誤原因後再試一次。",
                    notes=f"{queue_marker + '：' if queue_marker else ''}修正資訊：{correction[:200]}",
                    error=message,
                    prompt_signature=metadata_signature,
                )
                replace_result_by_index(target.index, failed)
            set_current(current_progress_payload("修正重辨失敗"))
            set_state(state="修正失敗")
            add_log(f"[修正重辨] 錯誤：{message}")
        finally:
            config.prompt = base_prompt
            if manage_running:
                set_state(running=False)

    def drain_reanalyze_queue(stop_event: threading.Event) -> None:
        while not stop_event.is_set():
            with state_lock:
                queue = app_state["reanalyze_queue"]
                if not queue:
                    return
                item = queue.pop(0)
            run_reanalyze_job(
                item["config"],
                int(item["pending_index"]),
                str(item["correction"]),
                stop_event,
                original_index=int(item.get("original_index", 0) or 0),
                queue_id=str(item.get("queue_id", "")),
                manage_running=False,
            )

    def has_reanalyze_queue() -> bool:
        with state_lock:
            return bool(app_state["reanalyze_queue"])

    def run_reanalyze_queue_worker(stop_event: threading.Event) -> None:
        try:
            drain_reanalyze_queue(stop_event)
        finally:
            set_state(running=False)

    class Handler(http.server.BaseHTTPRequestHandler):
        def log_message(self, format: str, *args: Any) -> None:
            return

        def send_json(self, data: dict[str, Any], status: int = 200) -> None:
            body = json.dumps(data, ensure_ascii=False).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def send_binary(self, body: bytes, content_type: str, status: int = 200) -> None:
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Cache-Control", "private, max-age=300, must-revalidate")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def read_json(self) -> dict[str, Any]:
            length = int(self.headers.get("Content-Length", "0") or "0")
            if length <= 0:
                return {}
            return json.loads(self.rfile.read(length).decode("utf-8"))

        def do_GET(self) -> None:
            parsed = urllib.parse.urlparse(self.path)
            if parsed.path == "/":
                body = build_web_app_html(settings).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                return
            if parsed.path == "/api/status":
                self.send_json(snapshot())
                return
            if parsed.path == "/api/prompts":
                self.send_json({"prompts": list_prompt_files()})
                return
            if parsed.path == "/api/key-status":
                query = urllib.parse.parse_qs(parsed.query)
                provider = (query.get("provider") or ["openai"])[0]
                self.send_json(cached_api_key_status(provider))
                return
            if parsed.path == "/api/pending-status":
                self.send_json(pending_results_status())
                return
            if parsed.path == "/api/thumbnail":
                query = urllib.parse.parse_qs(parsed.query)
                try:
                    index = int((query.get("index") or ["0"])[0] or 0)
                    content_type, body = thumbnail_for_index(index)
                    self.send_binary(body, content_type)
                except Exception:
                    content_type, body = create_thumbnail_response(Path(""))
                    self.send_binary(body, content_type, 404)
                return
            self.send_json({"error": "Not found"}, 404)

        def do_POST(self) -> None:
            parsed = urllib.parse.urlparse(self.path)
            try:
                if parsed.path == "/api/select-folder":
                    folder_path = choose_folder_dialog()
                    self.send_json({"ok": True, "folder": folder_path, "cancelled": not bool(folder_path)})
                    return
                if parsed.path == "/api/start":
                    with state_lock:
                        if app_state["running"]:
                            self.send_json({"error": "已有工作正在執行。"}, 409)
                            return
                    payload = self.read_json()
                    config, watch_mode = config_from_payload(payload)
                    save_settings(
                        {
                            "folder": str(config.folder),
                            "provider": config.provider,
                            "model": config.model,
                            "watch": watch_mode,
                            "prompt": config.prompt,
                            "prompt_name": str(payload.get("prompt_name", "default")).strip() or "default",
                        }
                    )
                    stop_event = threading.Event()
                    with state_lock:
                        app_state.update(
                            {
                                "state": "準備中",
                                "done": 0,
                                "total": 0,
                                "logs": [],
                                "results": [],
                                "completed_sources": set(),
                                "thumbnail_cache": {},
                                "current": current_progress_payload("準備中"),
                                "manifest": {},
                                "mode": "watch" if watch_mode else "batch",
                                "running": True,
                                "stop_event": stop_event,
                            }
                        )
                    worker = threading.Thread(
                        target=run_job,
                        args=(config, watch_mode, stop_event),
                        daemon=True,
                    )
                    with state_lock:
                        app_state["worker"] = worker
                    worker.start()
                    self.send_json({"ok": True})
                    return
                if parsed.path == "/api/resume":
                    with state_lock:
                        if app_state["running"]:
                            self.send_json({"error": "已有工作正在執行。"}, 409)
                            return
                    payload = self.read_json()
                    config, watch_mode = config_from_payload(payload)
                    if watch_mode:
                        self.send_json({"error": "續跑請先取消監看資料夾。"}, 400)
                        return
                    with state_lock:
                        current_results = list(app_state["results"])
                    completed_sources: set[str] = set()
                    if not current_results and pending_results_status().get("exists"):
                        current_results, completed_sources = load_pending_state()
                    elif current_results:
                        with state_lock:
                            completed_sources = set(app_state["completed_sources"])
                    if not current_results and not completed_sources:
                        self.send_json({"error": "目前沒有可續跑進度，請先按開始。"}, 400)
                        return
                    save_settings(
                        {
                            "folder": str(config.folder),
                            "provider": config.provider,
                            "model": config.model,
                            "watch": False,
                            "prompt": config.prompt,
                            "prompt_name": str(payload.get("prompt_name", "default")).strip() or "default",
                        }
                    )
                    stop_event = threading.Event()
                    with state_lock:
                        app_state.update(
                            {
                                "state": "準備續跑",
                                "done": len(current_results),
                                "total": len(current_results),
                                "results": reindex_results(current_results),
                                "completed_sources": completed_sources | completed_sources_from_results(current_results, config),
                                "current": current_progress_payload("準備續跑", total=len(current_results)),
                                "manifest": {},
                                "running": True,
                                "stop_event": stop_event,
                            }
                        )
                    worker = threading.Thread(
                        target=run_resume_job,
                        args=(config, stop_event),
                        daemon=True,
                    )
                    with state_lock:
                        app_state["worker"] = worker
                    worker.start()
                    self.send_json({"ok": True})
                    return
                if parsed.path == "/api/reanalyze-result":
                    payload = self.read_json()
                    target_index = int(payload.get("index", 0) or 0)
                    correction = str(payload.get("correction", "")).strip()
                    if target_index <= 0:
                        self.send_json({"error": "找不到要修正重辨的照片。"}, 400)
                        return
                    if not correction:
                        self.send_json({"error": "請輸入正確資訊，例如：這張是鹹蛋苦瓜，不是炒高麗菜。"}, 400)
                        return
                    config, _watch_mode = config_from_payload(payload)
                    should_start_worker = False
                    pending_index = 0
                    queue_id = ""
                    with state_lock:
                        current_results = list(app_state["results"])
                        source = next(
                            (result for result in current_results if int(result.index) == target_index),
                            None,
                        )
                        if source is None:
                            self.send_json({"error": "找不到要修正重辨的照片。"}, 404)
                            return
                        if source.status in {"pending_reanalyze", "processing", "superseded"}:
                            self.send_json({"error": "這筆已經在修正重辨流程中。"}, 409)
                            return
                        queue_id = f"R{int(app_state['next_reanalyze_id'])}"
                        app_state["next_reanalyze_id"] = int(app_state["next_reanalyze_id"]) + 1
                        remove_completed_source_for_result(app_state["completed_sources"], source)
                        source.status = "superseded"
                        source.notes = f"修正重辨 {queue_id}：已排入修正，完成後移除此列。"
                        pending_index = max([int(result.index) for result in current_results] or [0]) + 1
                        pending = ImageResult(
                            index=pending_index,
                            filename=source.filename,
                            source_path=source.source_path,
                            status="pending_reanalyze",
                            provider=config.provider,
                            model=config.model,
                            zh_summary="已排入修正重辨，會在目前任務之後執行。",
                            notes=f"修正重辨 {queue_id}：{correction[:200]}",
                            prompt_signature=metadata_signature_for_config(config),
                        )
                        app_state["results"].append(pending)
                        app_state["results"] = reindex_results(list(app_state["results"]))
                        pending_index = next(
                            (
                                int(result.index)
                                for result in app_state["results"]
                                if f"修正重辨 {queue_id}" in (result.notes or "")
                                and result.status == "pending_reanalyze"
                            ),
                            pending_index,
                        )
                        app_state["reanalyze_queue"].append(
                            {
                                "queue_id": queue_id,
                                "original_index": int(source.index),
                                "pending_index": pending_index,
                                "correction": correction,
                                "config": config,
                            }
                        )
                        if not app_state["running"]:
                            should_start_worker = True
                            stop_event = threading.Event()
                            app_state.update(
                                {
                                    "state": "準備修正重辨",
                                    "current": current_progress_payload("準備修正重辨", index=pending_index, total=len(app_state["results"])),
                                    "mode": "reanalyze",
                                    "running": True,
                                    "stop_event": stop_event,
                                }
                            )
                        else:
                            stop_event = app_state.get("stop_event") or threading.Event()
                        results_copy = list(app_state["results"])
                        completed_sources = set(app_state["completed_sources"])
                    save_pending_results(results_copy, completed_sources)
                    add_log(f"[修正重辨] 已排隊 {queue_id}：{source.filename}")
                    if should_start_worker:
                        worker = threading.Thread(
                            target=run_reanalyze_queue_worker,
                            args=(stop_event,),
                            daemon=True,
                        )
                        with state_lock:
                            app_state["worker"] = worker
                        worker.start()
                    self.send_json({"ok": True, "queued": True, "queue_id": queue_id, "pending_index": pending_index})
                    return
                if parsed.path == "/api/save-prompt":
                    payload = self.read_json()
                    name = str(payload.get("name", "default")).strip() or "default"
                    prompt = str(payload.get("prompt", ""))
                    path = save_prompt_file(name, prompt)
                    self.send_json({"ok": True, "name": safe_prompt_name(name), "path": str(path)})
                    return
                if parsed.path == "/api/load-prompt":
                    payload = self.read_json()
                    name = str(payload.get("name", "default")).strip() or "default"
                    self.send_json({"ok": True, "name": safe_prompt_name(name), "prompt": load_prompt_file(name)})
                    return
                if parsed.path == "/api/delete-prompt":
                    payload = self.read_json()
                    name = str(payload.get("name", "")).strip()
                    deleted_name = delete_prompt_file(name)
                    self.send_json({"ok": True, "name": deleted_name, "prompts": list_prompt_files()})
                    return
                if parsed.path == "/api/clear-key":
                    payload = self.read_json()
                    provider = str(payload.get("provider", "openai")).strip().lower()
                    clear_cached_api_key(provider)
                    self.send_json({"ok": True})
                    return
                if parsed.path == "/api/save-progress":
                    with state_lock:
                        results_copy = list(app_state["results"])
                        completed_sources = set(app_state["completed_sources"])
                        app_state["current"] = current_progress_payload("已儲存進度", total=len(results_copy))
                    self.send_json(save_pending_results(results_copy, completed_sources))
                    return
                if parsed.path == "/api/load-progress":
                    if not pending_results_status().get("exists"):
                        self.send_json({"error": "目前沒有已儲存進度。"}, 404)
                        return
                    with state_lock:
                        if app_state["running"]:
                            self.send_json({"error": "執行中不能載入進度，請先停止或等完成。"}, 409)
                            return
                    pending_results, completed_sources = load_pending_state()
                    with state_lock:
                        app_state["results"] = pending_results
                        app_state["done"] = len(pending_results)
                        app_state["total"] = len(pending_results)
                        app_state["completed_sources"] = completed_sources
                        app_state["state"] = "已載入進度"
                        app_state["current"] = current_progress_payload("已載入進度", total=len(pending_results))
                        app_state["manifest"] = {}
                    add_log(f"已載入 {len(pending_results)} 筆進度。")
                    self.send_json({"ok": True, "count": len(pending_results)})
                    return
                if parsed.path == "/api/delete-result":
                    payload = self.read_json()
                    target_index = int(payload.get("index", 0) or 0)
                    with state_lock:
                        current_results = list(app_state["results"])
                        removed_results = [
                            result for result in current_results if int(result.index) == target_index
                        ]
                        remaining = [result for result in current_results if int(result.index) != target_index]
                        if len(remaining) == len(current_results):
                            self.send_json({"error": "找不到要刪除的結果。"}, 404)
                            return
                        for removed in removed_results:
                            remove_completed_source_for_result(app_state["completed_sources"], removed)
                        app_state["results"] = reindex_results(remaining)
                        if not app_state["running"]:
                            app_state["done"] = len(app_state["results"])
                            app_state["total"] = len(app_state["results"])
                            app_state["current"] = current_progress_payload("已刪除一筆", total=len(app_state["results"]))
                        results_copy = list(app_state["results"])
                        completed_sources = set(app_state["completed_sources"])
                    saved = save_pending_results(results_copy, completed_sources)
                    add_log(f"已刪除第 {target_index} 筆，剩餘 {len(results_copy)} 筆；進度已更新。")
                    self.send_json({"ok": True, "count": len(results_copy), "saved": saved})
                    return
                if parsed.path == "/api/stop":
                    with state_lock:
                        stop_event = app_state.get("stop_event")
                    if stop_event:
                        stop_event.set()
                    set_current(current_progress_payload("停止中"))
                    add_log("收到停止要求。")
                    self.send_json({"ok": True})
                    return
                if parsed.path in {"/api/open-report", "/api/open-output"}:
                    with state_lock:
                        manifest = dict(app_state["manifest"])
                    target = manifest.get("html") if parsed.path.endswith("report") else manifest.get("output_dir")
                    if not target:
                        self.send_json({"error": "目前沒有可開啟的輸出。"}, 400)
                        return
                    webbrowser.open(Path(target).resolve().as_uri())
                    self.send_json({"ok": True})
                    return
                self.send_json({"error": "Not found"}, 404)
            except Exception as exc:
                self.send_json({"error": redact_sensitive(exc)}, 500)

    actual_port = find_available_port(port)
    server = http.server.ThreadingHTTPServer(("127.0.0.1", actual_port), Handler)
    url = f"http://127.0.0.1:{actual_port}/"
    print(f"Opening browser UI: {url}")
    webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


def run_gui() -> None:
    import tkinter as tk
    from tkinter import filedialog, font as tkfont, messagebox, scrolledtext, ttk

    class KeyworderApp(tk.Tk):
        def __init__(self) -> None:
            super().__init__()
            self.title(APP_NAME)
            self.geometry("1280x820")
            self.minsize(1080, 720)
            self.report_callback_exception = self._report_callback_exception

            self.messages: queue.Queue[tuple[str, Any]] = queue.Queue()
            self.worker: Optional[threading.Thread] = None
            self.stop_event: Optional[threading.Event] = None
            self.last_report: str = ""
            self.result_by_iid: dict[str, ImageResult] = {}
            self.folder_scan_after_id: Optional[str] = None
            self.folder_scan_seq = 0

            saved = load_saved_settings()
            provider = saved.get("provider", "openai")

            self.folder_var = tk.StringVar(value=saved.get("folder", ""))
            self.provider_var = tk.StringVar(value=provider)
            self.model_var = tk.StringVar(
                value=normalize_model_for_provider(
                    provider,
                    str(saved.get("model", PROVIDER_DEFAULT_MODELS.get(provider, "gpt-5.5"))),
                )
            )
            self.api_key_var = tk.StringVar(value="")
            self.max_images_var = tk.IntVar(value=MAX_IMAGES)
            self.max_side_var = tk.IntVar(value=1600)
            self.max_file_mb_var = tk.IntVar(value=DEFAULT_MAX_FILE_MB)
            self.daily_limit_var = tk.IntVar(value=DEFAULT_DAILY_API_LIMIT)
            self.show_key_var = tk.BooleanVar(value=False)
            self.watch_var = tk.BooleanVar(value=bool(saved.get("watch", False)))
            self.folder_summary_var = tk.StringVar(value="")
            self.security_summary_var = tk.StringVar(value="")
            self.status_var = tk.StringVar(value="待命")
            self.result_summary_var = tk.StringVar(value="尚未開始")
            self.selected_title_var = tk.StringVar(value="尚未選取結果")
            self.selected_status_var = tk.StringVar(value="")
            self.selected_notes_var = tk.StringVar(value="")
            self.selected_result: Optional[ImageResult] = None

            self._build_ui(saved.get("prompt", DEFAULT_PROMPT))
            self.folder_summary_var.set("選擇資料夾後會顯示照片數；也可按掃描")
            self._refresh_security_summary()
            self._bring_to_front()
            self.after(120, self._drain_messages)

        def _report_callback_exception(self, exc_type: Any, exc: BaseException, tb: Any) -> None:
            message = "".join(traceback.format_exception(exc_type, exc, tb))
            log_path = Path(__file__).with_name("stock_keyworder.log")
            try:
                with log_path.open("a", encoding="utf-8") as handle:
                    handle.write(f"\n[{dt.datetime.now().isoformat(timespec='seconds')}] GUI callback error\n")
                    handle.write(redact_sensitive(message, [self.api_key_var.get()]))
                    handle.write("\n")
            except Exception:
                pass
            messagebox.showerror(APP_NAME, redact_sensitive(exc, [self.api_key_var.get()]))

        def _bring_to_front(self) -> None:
            try:
                self.update_idletasks()
                self.deiconify()
                self.lift()
                self.attributes("-topmost", True)
                self.focus_force()
                self.after(900, lambda: self.attributes("-topmost", False))
            except Exception:
                pass

        def _build_ui(self, initial_prompt: str) -> None:
            style = ttk.Style(self)
            colors = {
                "bg": "#f4f6f8",
                "panel": "#ffffff",
                "text": "#17202a",
                "muted": "#667085",
                "line": "#cfd7e3",
                "field": "#ffffff",
                "select": "#2563eb",
                "select_soft": "#dbeafe",
                "button": "#e9eef5",
            }
            try:
                style.theme_use("clam")
            except Exception:
                pass
            self.configure(bg=colors["bg"])

            default_font = tkfont.nametofont("TkDefaultFont")
            header_font = default_font.copy()
            header_font.configure(size=18, weight="bold")
            section_font = default_font.copy()
            section_font.configure(weight="bold")
            small_font = default_font.copy()
            small_font.configure(size=max(default_font.cget("size") - 1, 9))

            style.configure(".", background=colors["bg"], foreground=colors["text"])
            style.configure("TFrame", background=colors["bg"])
            style.configure(
                "TLabel",
                background=colors["bg"],
                foreground=colors["text"],
                padding=(0, 2),
            )
            style.configure(
                "TButton",
                background=colors["button"],
                foreground=colors["text"],
                padding=(10, 7),
                bordercolor=colors["line"],
                lightcolor=colors["button"],
                darkcolor=colors["line"],
            )
            style.map(
                "TButton",
                background=[("active", "#d8e2ee"), ("pressed", "#c8d4e2")],
                foreground=[("disabled", "#98a2b3")],
            )
            style.configure("Primary.TButton", padding=(12, 9), font=section_font)
            style.configure(
                "TEntry",
                fieldbackground=colors["field"],
                foreground=colors["text"],
                insertcolor=colors["text"],
                bordercolor=colors["line"],
            )
            style.configure(
                "TCombobox",
                fieldbackground=colors["field"],
                foreground=colors["text"],
                background=colors["field"],
                arrowcolor=colors["text"],
                bordercolor=colors["line"],
            )
            style.configure(
                "TSpinbox",
                fieldbackground=colors["field"],
                foreground=colors["text"],
                insertcolor=colors["text"],
                bordercolor=colors["line"],
            )
            style.configure(
                "TCheckbutton",
                background=colors["bg"],
                foreground=colors["text"],
            )
            style.configure(
                "TProgressbar",
                background=colors["select"],
                troughcolor="#e5eaf1",
                bordercolor=colors["line"],
                lightcolor=colors["select"],
                darkcolor=colors["select"],
            )
            style.configure(
                "TLabelframe",
                background=colors["panel"],
                foreground=colors["text"],
                bordercolor=colors["line"],
                relief="solid",
            )
            style.configure(
                "TLabelframe.Label",
                background=colors["bg"],
                foreground=colors["text"],
            )
            style.configure("Step.TLabelframe", background=colors["panel"])
            style.configure(
                "Step.TLabelframe.Label",
                background=colors["bg"],
                foreground=colors["text"],
                font=section_font,
            )
            style.configure("Hint.TLabel", foreground=colors["muted"], font=small_font)
            style.configure("Status.TLabel", foreground=colors["text"], font=section_font)
            style.configure("Panel.TFrame", background=colors["panel"])
            style.configure(
                "Panel.TLabel",
                background=colors["panel"],
                foreground=colors["text"],
                padding=(0, 2),
            )
            style.configure("Panel.Hint.TLabel", background=colors["panel"], foreground=colors["muted"], font=small_font)
            style.configure("Panel.TCheckbutton", background=colors["panel"], foreground=colors["text"])
            style.configure(
                "Treeview",
                background=colors["field"],
                fieldbackground=colors["field"],
                foreground=colors["text"],
                bordercolor=colors["line"],
                rowheight=24,
            )
            style.configure(
                "Treeview.Heading",
                background="#eef2f7",
                foreground=colors["text"],
                font=section_font,
            )
            style.map(
                "Treeview",
                background=[("selected", colors["select"])],
                foreground=[("selected", "#ffffff")],
            )

            def configure_text_widget(widget: scrolledtext.ScrolledText) -> None:
                widget.configure(
                    background=colors["field"],
                    foreground=colors["text"],
                    insertbackground=colors["text"],
                    selectbackground=colors["select_soft"],
                    selectforeground=colors["text"],
                    relief="solid",
                    borderwidth=1,
                    highlightthickness=1,
                    highlightbackground=colors["line"],
                    highlightcolor=colors["select"],
                )

            root = ttk.Frame(self, padding=14)
            root.pack(fill="both", expand=True)
            root.columnconfigure(0, weight=0, minsize=420)
            root.columnconfigure(1, weight=1)
            root.rowconfigure(1, weight=1)

            header = ttk.Frame(root)
            header.grid(row=0, column=0, columnspan=2, sticky="ew", pady=(0, 12))
            header.columnconfigure(0, weight=1)
            ttk.Label(header, text=APP_NAME, font=header_font).grid(row=0, column=0, sticky="w")
            ttk.Label(
                header,
                textvariable=self.status_var,
                style="Status.TLabel",
            ).grid(row=0, column=1, sticky="e")
            left = ttk.Frame(root)
            left.grid(row=1, column=0, sticky="nsew", padx=(0, 12))
            left.columnconfigure(0, weight=1)
            left.rowconfigure(2, weight=1)

            source = ttk.LabelFrame(left, text="1  照片來源", padding=12, style="Step.TLabelframe")
            source.grid(row=0, column=0, sticky="ew", pady=(0, 10))
            source.columnconfigure(1, weight=1)

            ttk.Label(source, text="照片資料夾", style="Panel.TLabel").grid(row=0, column=0, sticky="w")
            folder_entry = ttk.Entry(source, textvariable=self.folder_var)
            folder_entry.grid(row=0, column=1, sticky="ew", padx=(8, 6))
            ttk.Button(source, text="選擇", command=self._choose_folder).grid(
                row=0, column=2, sticky="ew"
            )
            ttk.Button(source, text="掃描", command=self._schedule_folder_summary).grid(
                row=0, column=3, sticky="ew", padx=(6, 0)
            )
            ttk.Label(source, textvariable=self.folder_summary_var, style="Panel.Hint.TLabel").grid(
                row=1, column=1, columnspan=3, sticky="w", padx=(8, 0), pady=(3, 0)
            )

            model_box = ttk.LabelFrame(left, text="2  AI 模型", padding=12, style="Step.TLabelframe")
            model_box.grid(row=1, column=0, sticky="ew", pady=(0, 10))
            model_box.columnconfigure(1, weight=1)

            ttk.Label(model_box, text="Provider", style="Panel.TLabel").grid(row=0, column=0, sticky="w")
            provider_box = ttk.Combobox(
                model_box,
                textvariable=self.provider_var,
                values=["openai", "gemini"],
                state="readonly",
            )
            provider_box.grid(row=0, column=1, columnspan=2, sticky="ew", padx=(8, 0))
            provider_box.bind("<<ComboboxSelected>>", self._provider_changed)

            ttk.Label(model_box, text="Model 官方 API ID", style="Panel.TLabel").grid(row=1, column=0, sticky="w", pady=(8, 0))
            ttk.Entry(model_box, textvariable=self.model_var).grid(
                row=1, column=1, columnspan=2, sticky="ew", padx=(8, 0), pady=(8, 0)
            )

            ttk.Label(model_box, text="API Key", style="Panel.TLabel").grid(row=2, column=0, sticky="w")
            self.api_entry = ttk.Entry(model_box, textvariable=self.api_key_var, show="*")
            self.api_entry.grid(
                row=2, column=1, sticky="ew", padx=(8, 6)
            )
            ttk.Checkbutton(
                model_box,
                text="顯示",
                variable=self.show_key_var,
                command=self._toggle_key_visibility,
                style="Panel.TCheckbutton",
            ).grid(row=2, column=2, sticky="w")

            prompt_box = ttk.LabelFrame(left, text="3  圖庫需求 Prompt", padding=12, style="Step.TLabelframe")
            prompt_box.grid(row=2, column=0, sticky="nsew", pady=(0, 10))
            prompt_box.columnconfigure(0, weight=1)
            prompt_box.rowconfigure(1, weight=1)

            prompt_buttons = ttk.Frame(prompt_box, style="Panel.TFrame")
            prompt_buttons.grid(row=0, column=0, sticky="ew", pady=(0, 8))
            prompt_buttons.columnconfigure(1, weight=1)
            ttk.Button(prompt_buttons, text="通用模板", command=self._apply_default_prompt).grid(
                row=0, column=0, sticky="w"
            )
            ttk.Label(prompt_buttons, text="模板", style="Panel.Hint.TLabel").grid(
                row=0, column=1, sticky="e"
            )

            self.prompt_text = scrolledtext.ScrolledText(prompt_box, height=12, wrap="word")
            self.prompt_text.grid(
                row=1, column=0, sticky="nsew"
            )
            configure_text_widget(self.prompt_text)
            self.prompt_text.insert("1.0", initial_prompt)

            run_box = ttk.LabelFrame(left, text="4  執行", padding=12, style="Step.TLabelframe")
            run_box.grid(row=3, column=0, sticky="ew")
            run_box.columnconfigure(1, weight=1)

            ttk.Checkbutton(
                run_box,
                text="監看資料夾",
                variable=self.watch_var,
                style="Panel.TCheckbutton",
            ).grid(row=0, column=0, columnspan=4, sticky="w", pady=(2, 8))

            button_bar = ttk.Frame(run_box, style="Panel.TFrame")
            button_bar.grid(row=1, column=0, columnspan=4, sticky="ew", pady=(8, 0))
            button_bar.columnconfigure(0, weight=1)
            button_bar.columnconfigure(1, weight=1)
            self.start_button = ttk.Button(
                button_bar,
                text="開始",
                command=self._start,
                style="Primary.TButton",
            )
            self.start_button.grid(row=0, column=0, sticky="ew", padx=(0, 6))
            self.stop_button = ttk.Button(
                button_bar,
                text="停止",
                command=self._stop,
                state="disabled",
            )
            self.stop_button.grid(row=0, column=1, sticky="ew", padx=(6, 0))

            right = ttk.Frame(root)
            right.grid(row=1, column=1, sticky="nsew")
            right.rowconfigure(2, weight=3)
            right.rowconfigure(4, weight=2)
            right.rowconfigure(5, weight=1)
            right.columnconfigure(0, weight=1)

            status_box = ttk.LabelFrame(right, text="執行狀態", padding=12)
            status_box.grid(row=0, column=0, columnspan=2, sticky="ew")
            status_box.columnconfigure(0, weight=1)
            status_box.columnconfigure(1, weight=0)
            ttk.Label(status_box, textvariable=self.result_summary_var, style="Status.TLabel").grid(
                row=0, column=0, sticky="w"
            )
            self.progress_label = ttk.Label(status_box, text="0 / 0")
            self.progress_label.grid(row=0, column=1, sticky="e")
            self.progress = ttk.Progressbar(status_box, mode="determinate")
            self.progress.grid(row=1, column=0, columnspan=2, sticky="ew", pady=(8, 0))

            columns = ("filename", "status", "title", "description", "keywords", "notes", "copy_line")
            self.tree = ttk.Treeview(right, columns=columns, show="headings", height=14)
            self.tree.grid(row=2, column=0, sticky="nsew", pady=(10, 10))
            headings = {
                "filename": "檔名",
                "status": "狀態",
                "title": "標題",
                "description": "描述",
                "keywords": "關鍵字",
                "notes": "備註 / 錯誤",
                "copy_line": "整列複製內容",
            }
            widths = {
                "filename": 150,
                "status": 60,
                "title": 180,
                "description": 240,
                "keywords": 280,
                "notes": 160,
                "copy_line": 300,
            }
            for column in columns:
                self.tree.heading(column, text=headings[column])
                self.tree.column(column, width=widths[column], anchor="w")
            self.tree.bind("<<TreeviewSelect>>", self._show_selected_result)
            tree_scroll = ttk.Scrollbar(right, orient="vertical", command=self.tree.yview)
            tree_scroll.grid(row=2, column=1, sticky="ns", pady=(10, 10))
            self.tree.configure(yscrollcommand=tree_scroll.set)
            tree_xscroll = ttk.Scrollbar(right, orient="horizontal", command=self.tree.xview)
            tree_xscroll.grid(row=3, column=0, sticky="ew")
            self.tree.configure(xscrollcommand=tree_xscroll.set)

            detail = ttk.LabelFrame(right, text="選取結果 / 複製", padding=12)
            detail.grid(row=4, column=0, columnspan=2, sticky="nsew", pady=(0, 10))
            detail.columnconfigure(0, weight=1)
            detail.rowconfigure(2, weight=1)
            ttk.Label(detail, textvariable=self.selected_title_var, style="Status.TLabel").grid(
                row=0, column=0, sticky="w"
            )
            ttk.Label(detail, textvariable=self.selected_status_var, style="Hint.TLabel").grid(
                row=1, column=0, sticky="w", pady=(2, 6)
            )
            self.detail_text = scrolledtext.ScrolledText(detail, height=5, wrap="word")
            self.detail_text.grid(row=2, column=0, sticky="nsew")
            configure_text_widget(self.detail_text)
            self.detail_text.configure(state="disabled")
            ttk.Label(detail, textvariable=self.selected_notes_var, style="Hint.TLabel").grid(
                row=3, column=0, sticky="w", pady=(6, 0)
            )
            copy_bar = ttk.Frame(detail)
            copy_bar.grid(row=4, column=0, sticky="ew", pady=(8, 0))
            ttk.Button(copy_bar, text="複製標題", command=self._copy_selected_title).grid(
                row=0, column=0, sticky="w", padx=(0, 8)
            )
            ttk.Button(copy_bar, text="複製描述", command=self._copy_selected_description).grid(
                row=0, column=1, sticky="w", padx=(0, 8)
            )
            ttk.Button(copy_bar, text="複製標題+描述", command=self._copy_selected_title_description).grid(
                row=0, column=2, sticky="w", padx=(0, 8)
            )
            ttk.Button(copy_bar, text="複製關鍵字", command=self._copy_selected_keywords).grid(
                row=0, column=3, sticky="w", padx=(0, 8)
            )
            ttk.Button(copy_bar, text="複製整列", command=self._copy_selected_line).grid(
                row=0, column=4, sticky="w", padx=(0, 8)
            )
            ttk.Button(copy_bar, text="刪除該筆", command=self._delete_selected_result).grid(
                row=0, column=5, sticky="w", padx=(0, 8)
            )
            ttk.Button(copy_bar, text="儲存進度", command=self._save_progress).grid(
                row=0, column=6, sticky="w", padx=(0, 8)
            )
            ttk.Button(copy_bar, text="載入進度", command=self._load_progress).grid(
                row=0, column=7, sticky="w"
            )

            log_box = ttk.LabelFrame(right, text="Log", padding=8)
            log_box.grid(row=5, column=0, columnspan=2, sticky="nsew")
            log_box.columnconfigure(0, weight=1)
            log_box.rowconfigure(0, weight=1)
            self.log_text = scrolledtext.ScrolledText(log_box, height=8, wrap="word")
            self.log_text.grid(row=0, column=0, sticky="nsew")
            configure_text_widget(self.log_text)

            self.folder_var.trace_add("write", lambda *_: self._schedule_folder_summary())
            self.provider_var.trace_add("write", lambda *_: self._refresh_security_summary())
            self.model_var.trace_add("write", lambda *_: self._refresh_security_summary())

        def _choose_folder(self) -> None:
            folder = filedialog.askdirectory()
            if folder:
                self.folder_var.set(folder)
                self._schedule_folder_summary(delay_ms=80)

        def _provider_changed(self, _event: Any = None) -> None:
            provider = self.provider_var.get()
            current = self.model_var.get().strip()
            self.model_var.set(normalize_model_for_provider(provider, current))

        def _toggle_key_visibility(self) -> None:
            self.api_entry.configure(show="" if self.show_key_var.get() else "*")

        def _apply_default_prompt(self) -> None:
            self.prompt_text.delete("1.0", "end")
            self.prompt_text.insert("1.0", DEFAULT_PROMPT)

        def _schedule_folder_summary(self, delay_ms: int = 650) -> None:
            if self.folder_scan_after_id:
                try:
                    self.after_cancel(self.folder_scan_after_id)
                except Exception:
                    pass
            self.folder_scan_after_id = self.after(delay_ms, self._refresh_folder_summary)

        def _refresh_folder_summary(self) -> None:
            self.folder_scan_after_id = None
            text = self.folder_var.get().strip()
            try:
                limit = int(self.max_images_var.get())
            except Exception:
                limit = MAX_IMAGES
            try:
                max_file_mb = int(self.max_file_mb_var.get())
            except Exception:
                max_file_mb = DEFAULT_MAX_FILE_MB

            if not text:
                self.folder_summary_var.set("尚未選擇照片資料夾")
                return
            self.folder_scan_seq += 1
            seq = self.folder_scan_seq
            self.folder_summary_var.set("正在掃描照片數...")
            threading.Thread(
                target=self._scan_folder_summary_worker,
                args=(seq, text, limit, max_file_mb),
                daemon=True,
            ).start()

        def _scan_folder_summary_worker(
            self,
            seq: int,
            folder_text: str,
            limit: int,
            max_file_mb: int,
        ) -> None:
            summary = self._build_folder_summary(folder_text, limit, max_file_mb)
            self.messages.put(("folder_summary", {"seq": seq, "text": summary}))

        def _build_folder_summary(self, folder_text: str, limit: int, max_file_mb: int) -> str:
            folder = Path(folder_text).expanduser()
            if not folder.exists() or not folder.is_dir():
                return "資料夾不存在"
            try:
                images = [
                    path
                    for path in folder.iterdir()
                    if path.is_file() and path.suffix.lower() in IMAGE_EXTENSIONS
                ]
            except OSError:
                return "無法讀取資料夾"
            count = len(images)
            eligible = count_api_eligible_images(images, max_file_mb)
            oversized = count - eligible
            if count > limit:
                return f"找到 {count} 張，超過目前上限 {limit} 張"
            if oversized:
                return f"找到 {count} 張；{eligible} 張可送 API，{oversized} 張超過 {max_file_mb} MB 會跳過"
            return f"找到 {count} 張支援格式照片，皆低於 {max_file_mb} MB"

        def _refresh_security_summary(self) -> None:
            try:
                daily_limit = int(self.daily_limit_var.get())
            except Exception:
                daily_limit = DEFAULT_DAILY_API_LIMIT
            temp_config = RunConfig(
                folder=Path("."),
                provider=self.provider_var.get().strip().lower() or "openai",
                model=self.model_var.get().strip() or "unknown",
                api_key="",
                prompt="",
                daily_limit=daily_limit,
            )
            used = get_persisted_today_api_attempts(temp_config)
            remaining = max(daily_limit - used, 0)
            self.security_summary_var.set(f"今日已用 {used}，剩餘 {remaining}")

        def _set_detail_text(self, value: str) -> None:
            self.detail_text.configure(state="normal")
            self.detail_text.delete("1.0", "end")
            self.detail_text.insert("1.0", value)
            self.detail_text.configure(state="disabled")

        def _collect_config(self) -> RunConfig:
            folder = Path(self.folder_var.get()).expanduser()
            provider = self.provider_var.get().strip().lower()
            model = normalize_model_for_provider(
                provider,
                self.model_var.get().strip() or PROVIDER_DEFAULT_MODELS.get(provider, ""),
            )
            prompt = self.prompt_text.get("1.0", "end").strip()
            return RunConfig(
                folder=folder,
                provider=provider,
                model=model,
                api_key=self.api_key_var.get().strip(),
                prompt=prompt,
                output_dir=None,
                save_outputs=False,
                max_images=int(self.max_images_var.get()),
                max_side=int(self.max_side_var.get()),
                max_file_mb=int(self.max_file_mb_var.get()),
                daily_limit=int(self.daily_limit_var.get()),
            )

        def _planned_image_count(self, config: RunConfig, watch_mode: bool) -> int:
            if watch_mode:
                return config.max_images
            images = discover_images(config.folder, config.max_images)
            return count_api_eligible_images(images, config.max_file_mb)

        def _confirm_api_usage(self, config: RunConfig, watch_mode: bool) -> bool:
            try:
                prepare_run(config)
                planned_images = self._planned_image_count(config, watch_mode)
                ensure_daily_limit(
                    config,
                    1 if watch_mode else (1 if config.reuse_similar_images and planned_images else planned_images),
                )
            except Exception as exc:
                messagebox.showerror(APP_NAME, redact_sensitive(exc, [config.api_key]))
                return False

            remaining = get_daily_remaining(config)
            needs_confirmation = watch_mode or planned_images >= CONFIRM_API_CALLS_THRESHOLD
            if not needs_confirmation:
                return True

            mode = "監看模式" if watch_mode else "批次分析"
            message = (
                f"{mode}將使用 {config.provider} / {config.model}\n\n"
                f"預計 API 照片數：{planned_images}\n"
                f"每張非沿用照片至少 1 次 API request；重試會額外消耗。\n"
                f"相似圖沿用：{'開啟' if config.reuse_similar_images else '關閉'}。\n"
                f"目前剩餘可用量：{remaining}\n\n"
                "API key 不會寫入設定檔或結果表。"
            )
            return messagebox.askyesno("確認 API 使用量", message)

        def _start(self) -> None:
            if self.worker and self.worker.is_alive():
                return
            try:
                config = self._collect_config()
            except Exception as exc:
                messagebox.showerror(APP_NAME, str(exc))
                return
            watch_mode = self.watch_var.get()
            if not self._confirm_api_usage(config, watch_mode):
                return

            self.tree.delete(*self.tree.get_children())
            self.result_by_iid.clear()
            self.selected_result = None
            self.selected_title_var.set("尚未選取結果")
            self.selected_status_var.set("")
            self.selected_notes_var.set("")
            self._set_detail_text("")
            self.progress.configure(value=0, maximum=1)
            self.progress_label.configure(text="0 / 0")
            self.last_report = ""
            self.status_var.set("執行中")
            self.result_summary_var.set("正在準備")
            self._log("開始監看資料夾。" if watch_mode else "開始批次分析。")

            save_settings(
                {
                    "folder": str(config.folder),
                    "provider": config.provider,
                    "model": config.model,
                    "watch": watch_mode,
                    "prompt": config.prompt,
                }
            )

            self.stop_event = threading.Event()
            self.start_button.configure(state="disabled")
            self.stop_button.configure(state="normal")
            self.worker = threading.Thread(
                target=self._run_worker,
                args=(config, self.stop_event, watch_mode),
                daemon=True,
            )
            self.worker.start()

        def _stop(self) -> None:
            if self.stop_event:
                self.stop_event.set()
                self.status_var.set("停止中")
                self._log("收到停止要求。")

        def _run_worker(
            self,
            config: RunConfig,
            stop_event: threading.Event,
            watch_mode: bool,
        ) -> None:
            def progress(kind: str, payload: Any) -> None:
                self.messages.put((kind, payload))

            try:
                if watch_mode:
                    watch_folder(config, progress=progress, stop_event=stop_event)
                else:
                    process_folder(config, progress=progress, stop_event=stop_event)
            except Exception as exc:
                self.messages.put(("error", redact_sensitive(exc, [config.api_key])))
                self.messages.put(("trace", redact_sensitive(traceback.format_exc(), [config.api_key])))

        def _drain_messages(self) -> None:
            try:
                while True:
                    kind, payload = self.messages.get_nowait()
                    if kind == "folder_summary":
                        if int(payload.get("seq", 0)) == self.folder_scan_seq:
                            self.folder_summary_var.set(str(payload.get("text", "")))
                    elif kind == "scan":
                        total = int(payload["total"])
                        self.progress.configure(value=0, maximum=max(total, 1))
                        self.progress_label.configure(text=f"0 / {total}")
                        if payload.get("mode") == "watch":
                            self.result_summary_var.set(f"監看中，最多處理 {total} 張")
                            self._log(f"監看上限 {total} 張照片。")
                        else:
                            self.result_summary_var.set(f"待處理 {total} 張")
                            self._log(f"找到 {total} 張照片。")
                    elif kind == "log":
                        self._log(str(payload))
                    elif kind == "progress":
                        done = int(payload["done"])
                        total = int(payload["total"])
                        self.progress.configure(value=done, maximum=max(total, 1))
                        self.progress_label.configure(text=f"{done} / {total}")
                        self.result_summary_var.set(f"已處理 {done} / {total}")
                    elif kind == "result":
                        self._append_result(payload)
                    elif kind == "done":
                        self.last_report = payload.get("html", "")
                        self.status_var.set("完成")
                        self.result_summary_var.set(
                            f"{payload['ok_count']} 成功，{payload['error_count']} 錯誤"
                        )
                        self._log(
                            f"完成：{payload['ok_count']} 成功，{payload['error_count']} 錯誤。"
                        )
                        if payload.get("csv"):
                            self._log(f"CSV：{payload['csv']}")
                        if payload.get("html"):
                            self._log(f"HTML：{payload['html']}")
                        if not payload.get("csv") and not payload.get("html"):
                            self._log("結果已顯示在右側表格，可直接複製。")
                        self._refresh_security_summary()
                        self.start_button.configure(state="normal")
                        self.stop_button.configure(state="disabled")
                    elif kind == "saved":
                        self.last_report = payload.get("html", "")
                        self.result_summary_var.set(
                            f"結果已更新：{payload['ok_count']} 成功，{payload['error_count']} 錯誤"
                        )
                        self._log(
                            f"已更新結果：{payload['ok_count']} 成功，{payload['error_count']} 錯誤。"
                        )
                        self._refresh_security_summary()
                    elif kind == "error":
                        self.status_var.set("錯誤")
                        self.result_summary_var.set("執行失敗")
                        self._log(f"錯誤：{payload}")
                        messagebox.showerror(APP_NAME, str(payload))
                        self.start_button.configure(state="normal")
                        self.stop_button.configure(state="disabled")
                    elif kind == "trace":
                        self._log(str(payload))
            except queue.Empty:
                pass
            self.after(120, self._drain_messages)

        def _append_result(self, result: ImageResult) -> None:
            groups = result.keyword_groups or [{"name": "Keywords", "keywords": result.keywords or []}]
            keywords = " | ".join(
                f"{group.get('name') or 'Keywords'} ({len(split_list(group.get('keywords')))}): "
                f"{', '.join(split_list(group.get('keywords')))}"
                for group in groups
            )
            notes = result.notes or result.error
            iid = self.tree.insert(
                "",
                "end",
                values=(
                    result.filename,
                    "完成" if result.status == "ok" else "錯誤",
                    result.title,
                    result.description,
                    keywords,
                    notes,
                    result.copy_line,
                ),
            )
            self.result_by_iid[iid] = result

        def _show_selected_result(self, _event: Any = None) -> None:
            selection = self.tree.selection()
            if not selection:
                return
            result = self.result_by_iid.get(selection[0])
            if not result:
                return
            self.selected_result = result
            self.selected_title_var.set(result.title or result.filename)
            self.selected_status_var.set(
                f"{'完成' if result.status == 'ok' else '錯誤'} · {result.filename}"
            )
            groups = result.keyword_groups or [{"name": "Keywords", "keywords": result.keywords or [], "copy_line": result.copy_line}]
            group_lines: list[str] = []
            for group in groups:
                group_keywords = split_list(group.get("keywords"))
                group_lines.extend(
                    [
                        f"{group.get('name') or 'Keywords'} ({len(group_keywords)} keywords):",
                        ", ".join(group_keywords),
                        f"Copy line: {group.get('copy_line') or result.copy_line}",
                        "",
                    ]
                )
            detail = "\n".join(
                [
                    f"Description: {result.description}",
                    "",
                    *group_lines,
                    f"Copy line: {result.copy_line}",
                ]
            ).strip()
            self._set_detail_text(detail)
            self.selected_notes_var.set(result.notes or result.error)

        def _copy_to_clipboard(self, value: str) -> None:
            self.clipboard_clear()
            self.clipboard_append(value)
            self.status_var.set("已複製")

        def _copy_selected_keywords(self) -> None:
            if not self.selected_result:
                return
            self._copy_to_clipboard(", ".join(self.selected_result.keywords or []))

        def _copy_selected_title(self) -> None:
            if not self.selected_result:
                return
            self._copy_to_clipboard(self.selected_result.title)

        def _copy_selected_description(self) -> None:
            if not self.selected_result:
                return
            self._copy_to_clipboard(self.selected_result.description)

        def _copy_selected_title_description(self) -> None:
            if not self.selected_result:
                return
            self._copy_to_clipboard(f"{self.selected_result.title}\t{self.selected_result.description}")

        def _copy_selected_line(self) -> None:
            if not self.selected_result:
                return
            self._copy_to_clipboard(self.selected_result.copy_line)

        def _current_results(self) -> list[ImageResult]:
            results = [
                self.result_by_iid[iid]
                for iid in self.tree.get_children()
                if iid in self.result_by_iid
            ]
            return reindex_results(results)

        def _save_progress(self) -> None:
            try:
                payload = save_pending_results(self._current_results())
            except Exception as exc:
                messagebox.showerror(APP_NAME, str(exc))
                return
            self.status_var.set(f"已儲存 {payload['count']} 筆進度")
            self._log(f"已儲存 {payload['count']} 筆進度。")

        def _load_progress(self) -> None:
            if self.worker and self.worker.is_alive():
                messagebox.showerror(APP_NAME, "執行中不能載入進度，請先停止或等完成。")
                return
            if not pending_results_status().get("exists"):
                messagebox.showinfo(APP_NAME, "目前沒有已儲存進度。")
                return
            if self.tree.get_children() and not messagebox.askyesno(
                APP_NAME,
                "載入進度會覆蓋目前畫面清單，確定繼續？",
            ):
                return
            try:
                loaded = load_pending_results()
            except Exception as exc:
                messagebox.showerror(APP_NAME, str(exc))
                return
            self.tree.delete(*self.tree.get_children())
            self.result_by_iid.clear()
            for result in loaded:
                self._append_result(result)
            self.selected_result = None
            self.selected_title_var.set("尚未選取結果")
            self.selected_status_var.set("")
            self.selected_notes_var.set("")
            self._set_detail_text("")
            self.progress.configure(value=len(loaded), maximum=max(len(loaded), 1))
            self.progress_label.configure(text=f"{len(loaded)} / {len(loaded)}")
            self.result_summary_var.set(f"已載入 {len(loaded)} 筆進度")
            self.status_var.set("已載入進度")
            self._log(f"已載入 {len(loaded)} 筆進度。")

        def _delete_selected_result(self) -> None:
            selection = self.tree.selection()
            if not selection:
                return
            iid = selection[0]
            result = self.result_by_iid.get(iid)
            if not result:
                return
            if not messagebox.askyesno(
                APP_NAME,
                f"只會從清單移除，不會刪除原始照片。\n\n確定刪除：{result.filename}？",
            ):
                return
            self.tree.delete(iid)
            self.result_by_iid.pop(iid, None)
            self.selected_result = None
            self.selected_title_var.set("尚未選取結果")
            self.selected_status_var.set("")
            self.selected_notes_var.set("")
            self._set_detail_text("")
            try:
                payload = save_pending_results(self._current_results())
            except Exception as exc:
                messagebox.showerror(APP_NAME, str(exc))
                return
            self.result_summary_var.set(f"剩餘 {payload['count']} 筆")
            self.status_var.set("已刪除並儲存進度")
            self._log(f"已刪除 {result.filename}；剩餘 {payload['count']} 筆。")

        def _log(self, message: str) -> None:
            self.log_text.insert("end", f"{dt.datetime.now().strftime('%H:%M:%S')}  {message}\n")
            self.log_text.see("end")

        def _open_report(self) -> None:
            target = self.last_report
            if target:
                webbrowser.open(Path(target).resolve().as_uri())

    app = KeyworderApp()
    app.mainloop()


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=APP_NAME)
    parser.add_argument("--folder", help="照片資料夾。未提供時會開啟 GUI。")
    parser.add_argument("--output-dir", help="輸出資料夾。預設建立在照片資料夾內。")
    parser.add_argument("--provider", choices=["openai", "gemini"], default="openai")
    parser.add_argument("--model", help="模型名稱。")
    parser.add_argument("--api-key", default="", help="API key。也可用 OPENAI_API_KEY/GEMINI_API_KEY。")
    parser.add_argument("--api-key-file", help="從文字檔讀取 API key，避免 key 出現在 shell history。")
    parser.add_argument("--prompt", default=DEFAULT_PROMPT, help="自訂圖庫需求 prompt。")
    parser.add_argument("--prompt-file", help="從文字檔讀取自訂 prompt。")
    parser.add_argument("--max-images", type=int, default=MAX_IMAGES)
    parser.add_argument("--max-side", type=int, default=1600)
    parser.add_argument("--max-file-mb", type=int, default=DEFAULT_MAX_FILE_MB)
    parser.add_argument("--timeout", type=int, default=180)
    parser.add_argument("--retries", type=int, default=DEFAULT_RETRY_COUNT)
    parser.add_argument("--daily-limit", type=int, default=DEFAULT_DAILY_API_LIMIT)
    parser.add_argument("--no-reuse-similar", action="store_true", help="關閉本機相似照片沿用 metadata 的節省 API 策略。")
    parser.add_argument("--similar-threshold", type=int, default=DEFAULT_SIMILARITY_THRESHOLD)
    parser.add_argument("--yes", action="store_true", help="跳過大量 API 使用確認。")
    parser.add_argument("--watch", action="store_true", help="監看資料夾，新照片穩定後自動分析。")
    parser.add_argument("--watch-interval", type=float, default=5.0, help="監看輪詢秒數。")
    parser.add_argument("--settle-seconds", type=float, default=3.0, help="新照片多久未修改才開始分析。")
    parser.add_argument("--tk-gui", action="store_true", help="使用舊版 Tk 桌面介面。預設使用瀏覽器介面。")
    parser.add_argument("--web-port", type=int, default=8765, help="瀏覽器介面的本機起始連接埠。")
    return parser.parse_args(argv)


def run_cli(args: argparse.Namespace) -> int:
    provider = args.provider
    model = args.model or PROVIDER_DEFAULT_MODELS[provider]
    prompt = args.prompt
    if args.prompt_file:
        prompt = Path(args.prompt_file).read_text(encoding="utf-8")
    api_key = args.api_key
    if args.api_key_file:
        api_key = Path(args.api_key_file).read_text(encoding="utf-8").strip()

    config = RunConfig(
        folder=Path(args.folder).expanduser(),
        provider=provider,
        model=model,
        api_key=api_key,
        prompt=prompt,
        output_dir=Path(args.output_dir).expanduser() if args.output_dir else None,
        max_images=args.max_images,
        max_side=args.max_side,
        max_file_mb=args.max_file_mb,
        timeout_seconds=args.timeout,
        retry_count=args.retries,
        daily_limit=args.daily_limit,
        reuse_similar_images=not args.no_reuse_similar,
        similar_threshold=args.similar_threshold,
    )

    try:
        prepare_run(config)
        planned_images = (
            config.max_images
            if args.watch
            else count_api_eligible_images(discover_images(config.folder, config.max_images), config.max_file_mb)
        )
        ensure_daily_limit(
            config,
            1 if args.watch else (1 if config.reuse_similar_images and planned_images else planned_images),
        )
    except Exception as exc:
        print(redact_sensitive(exc, [config.api_key]), file=sys.stderr)
        return 2

    if (args.watch or planned_images >= CONFIRM_API_CALLS_THRESHOLD) and not args.yes:
        remaining = get_daily_remaining(config)
        message = (
            f"Provider/model: {config.provider} / {config.model}\n"
            f"Planned API images: {planned_images}\n"
            f"Max file size: {config.max_file_mb} MB; larger files are skipped without API calls.\n"
            f"Each non-reused image uses at least 1 API request; retries use more.\n"
            f"Similar image reuse: {'on' if config.reuse_similar_images else 'off'}.\n"
            f"Daily limit: {config.daily_limit}; remaining now: {remaining}\n"
            "Type YES to continue: "
        )
        if not sys.stdin.isatty():
            print("Large or watch run requires --yes in non-interactive mode.", file=sys.stderr)
            return 2
        answer = input(message)
        if answer != "YES":
            print("Cancelled.", file=sys.stderr)
            return 1

    def progress(kind: str, payload: Any) -> None:
        if kind == "scan":
            print(f"Found {payload['total']} images")
        elif kind == "log":
            print(redact_sensitive(payload, [config.api_key]))
        elif kind == "progress":
            print(f"Progress {payload['done']}/{payload['total']}")
        elif kind == "done":
            print(f"CSV: {payload['csv']}")
            print(f"HTML: {payload['html']}")
        elif kind == "saved":
            print(f"Updated: {payload['html']}")

    try:
        if args.watch:
            manifest = watch_folder(
                config,
                interval_seconds=args.watch_interval,
                settle_seconds=args.settle_seconds,
                progress=progress,
            )
        else:
            manifest = process_folder(config, progress=progress)
    except KeyboardInterrupt:
        print("Stopped.")
        return 130
    return 0 if manifest["error_count"] == 0 else 2


def main(argv: Optional[list[str]] = None) -> int:
    args = parse_args(argv or sys.argv[1:])
    if not args.folder:
        if args.tk_gui:
            run_gui()
        else:
            run_web_gui(args.web_port)
        return 0
    return run_cli(args)


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        log_path = Path(__file__).with_name("stock_keyworder.log")
        message = traceback.format_exc()
        try:
            with log_path.open("a", encoding="utf-8") as handle:
                handle.write(f"\n[{dt.datetime.now().isoformat(timespec='seconds')}] Fatal error\n")
                handle.write(redact_sensitive(message))
                handle.write("\n")
        except Exception:
            pass
        print(redact_sensitive(exc), file=sys.stderr)
        print(f"Log file: {log_path}", file=sys.stderr)
        raise
