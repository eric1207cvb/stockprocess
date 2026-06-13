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
    "gemini": "gemini-3.5-flash",
}
PROVIDER_MODEL_SUGGESTIONS = {
    "openai": ["gpt-5.5"],
    "gemini": ["gemini-3.5-flash", "gemini-2.5-flash", "gemini-2.5-pro"],
}
CONFIG_PATH = Path.home() / ".stock_keyworder_config.json"
USAGE_PATH = Path.home() / ".stock_keyworder_usage.json"
PROMPT_DIR = Path.home() / ".stock_keyworder_prompts"
KEY_CACHE_PATH = Path.home() / ".stock_keyworder_keys.json"
KEYCHAIN_SERVICE = "Stock Keyworder"
PENDING_PATH = Path.home() / ".stock_keyworder_pending.json"
DEFAULT_DAILY_API_LIMIT = 500
MAX_DAILY_API_LIMIT = 10000
CONFIRM_API_CALLS_THRESHOLD = 25
MAX_RETRY_COUNT = 3
DEFAULT_MAX_FILE_MB = 64
MAX_FILE_MB = 512

DEFAULT_PROMPT = """請為國際圖庫上架產生英文 metadata。

需求：
- Title 使用自然英文，最多 80 個字元，不要堆疊關鍵字。
- Description 使用 1 句自然英文，描述照片主要內容、構圖、用途。
- Keywords 輸出 35 到 49 個英文關鍵字，最重要的 10 個放最前面。
- 避免臆測不可確認的品牌、地點、名人、族群、職業或事件。
- 若畫面有人臉、可識別人物、商標、車牌、受保護藝術品，請在 notes 標示可能需要 release 或有退件風險。
- 不要輸出 hashtag，不要重複關鍵字，不要加入不存在的物件。
"""

METADATA_JSON_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "title": {"type": "string"},
        "description": {"type": "string"},
        "keywords": {"type": "array", "items": {"type": "string"}},
        "categories": {"type": "array", "items": {"type": "string"}},
        "notes": {"type": "string"},
        "copy_line": {"type": "string"},
    },
    "required": ["title", "description", "keywords", "categories", "notes", "copy_line"],
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
    retry_count: int = 1
    daily_limit: int = DEFAULT_DAILY_API_LIMIT
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
    keywords: Optional[list[str]] = None
    categories: Optional[list[str]] = None
    notes: str = ""
    copy_line: str = ""
    error: str = ""
    thumbnail: str = ""

    def csv_row(self) -> dict[str, str]:
        return {
            "index": str(self.index),
            "filename": self.filename,
            "status": self.status,
            "title": self.title,
            "description": self.description,
            "keywords": ", ".join(self.keywords or []),
            "categories": ", ".join(self.categories or []),
            "notes": self.notes,
            "copy_line": self.copy_line,
            "error": redact_sensitive(self.error),
            "provider": self.provider,
            "model": self.model,
            "source_path": self.source_path,
            "thumbnail": self.thumbnail,
        }


ProgressCallback = Callable[[str, Any], None]


class UsageLimitError(RuntimeError):
    pass


def provider_for_model(model: str) -> str:
    text = model.strip().lower()
    if text.startswith("gemini-"):
        return "gemini"
    if text.startswith(("gpt-", "o1", "o3", "o4", "o5", "chatgpt-")):
        return "openai"
    return ""


def normalize_model_for_provider(provider: str, model: str) -> str:
    provider = provider.strip().lower()
    text = model.strip()
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


def file_limit_error(path: Path, max_file_mb: int) -> str:
    try:
        size_text = f"{file_size_mb(path):.1f} MB"
    except OSError:
        size_text = "未知大小"
    return f"單檔大小 {size_text} 超過上限 {max_file_mb} MB，已跳過且未呼叫 API。"


def get_effective_api_key(provider: str, api_key: str) -> str:
    key = api_key.strip()
    if key:
        return key
    env_name = "OPENAI_API_KEY" if provider == "openai" else "GEMINI_API_KEY"
    return os.environ.get(env_name, "").strip()


def build_metadata_prompt(user_prompt: str, filename: str) -> str:
    prompt = user_prompt.strip() or DEFAULT_PROMPT
    return f"""你是專業圖庫照片 metadata 標注員。請根據圖片內容與使用者需求產生可上架的資料。

使用者需求：
{prompt}

檔名：{filename}

請只輸出一個 JSON object，不要 markdown，不要額外說明。JSON schema：
{{
  "title": "string",
  "description": "string",
  "keywords": ["keyword 1", "keyword 2"],
  "categories": ["category 1", "category 2"],
  "notes": "string",
  "copy_line": "title<TAB>description<TAB>keyword1, keyword2, keyword3"
}}
"""


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
        raise RuntimeError(redact_sensitive(f"HTTP {exc.code}: {error_body[:1200]}")) from exc
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
        "max_output_tokens": 1400,
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
            "responseFormat": {
                "text": {
                    "mimeType": "application/json",
                    "schema": METADATA_JSON_SCHEMA,
                }
            },
            "maxOutputTokens": 1400,
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

    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        start = raw.find("{")
        end = raw.rfind("}")
        if start == -1 or end == -1 or end <= start:
            raise ValueError(f"模型回應不是 JSON：{raw[:300]}")
        parsed = json.loads(raw[start : end + 1])

    if isinstance(parsed, list):
        if not parsed:
            raise ValueError("模型回應 JSON list 為空。")
        parsed = parsed[0]
    if not isinstance(parsed, dict):
        raise ValueError("模型回應 JSON 不是 object。")
    return parsed


def split_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        items = value
    else:
        items = re.split(r"[,;\n]+", str(value))

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


def normalize_metadata(data: dict[str, Any]) -> dict[str, Any]:
    title = str(data.get("title", "")).strip()
    description = str(data.get("description", "")).strip()
    keywords = split_list(data.get("keywords"))
    categories = split_list(data.get("categories"))
    notes = str(data.get("notes", "")).strip()
    copy_line = str(data.get("copy_line", "")).strip()
    if not copy_line:
        copy_line = f"{title}\t{description}\t{', '.join(keywords)}"
    return {
        "title": title,
        "description": description,
        "keywords": keywords,
        "categories": categories,
        "notes": notes,
        "copy_line": copy_line,
    }


def analyze_one_image(config: RunConfig, image_path: Path, api_key: str) -> dict[str, Any]:
    prompt = build_metadata_prompt(config.prompt, image_path.name)
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
) -> list[ImageResult]:
    results: list[ImageResult] = []
    total = total_count or len(images)
    for offset, image_path in enumerate(images):
        index = start_index + offset
        if stop_event and stop_event.is_set():
            if progress:
                progress("log", "已停止，正在輸出目前完成的結果。")
            break

        if progress:
            progress("log", f"[{index}/{total}] 分析 {image_path.name}")

        result = ImageResult(
            index=index,
            filename=image_path.name,
            source_path=str(image_path),
            status="ok",
            provider=config.provider,
            model=config.model,
            keywords=[],
            categories=[],
        )

        if not is_within_file_limit(image_path, config.max_file_mb):
            result.status = "error"
            result.error = file_limit_error(image_path, config.max_file_mb)
            results.append(result)
            if progress:
                progress("result", result)
                progress("progress", {"done": completed_before + len(results), "total": total})
                progress("log", f"  跳過 {image_path.name}：{result.error}")
            continue

        try:
            raw_metadata: Optional[dict[str, Any]] = None
            last_error: Optional[Exception] = None
            for attempt in range(config.retry_count + 1):
                try:
                    ensure_daily_limit(config, 1)
                    record_api_attempt(config)
                    raw_metadata = analyze_one_image(config, image_path, api_key)
                    break
                except UsageLimitError:
                    raise
                except Exception as exc:
                    last_error = exc
                    if attempt < config.retry_count:
                        wait_seconds = 2 + attempt * 2
                        if progress:
                            progress(
                                "log",
                                f"  重試 {attempt + 1}/{config.retry_count}："
                                f"{redact_sensitive(exc, [api_key, config.api_key])}",
                            )
                        time.sleep(wait_seconds)
            if raw_metadata is None:
                raise last_error or RuntimeError("分析失敗。")
            normalized = normalize_metadata(raw_metadata)
            result.title = normalized["title"]
            result.description = normalized["description"]
            result.keywords = normalized["keywords"]
            result.categories = normalized["categories"]
            result.notes = normalized["notes"]
            result.copy_line = normalized["copy_line"]
        except UsageLimitError as exc:
            result.status = "error"
            result.error = redact_sensitive(exc, [api_key, config.api_key])
            results.append(result)
            if progress:
                progress("result", result)
                progress("progress", {"done": completed_before + len(results), "total": total})
                progress("log", result.error)
            break
        except Exception as exc:
            result.status = "error"
            result.error = redact_sensitive(exc, [api_key, config.api_key])

        results.append(result)
        if progress:
            progress("result", result)
            progress("progress", {"done": completed_before + len(results), "total": total})

    return results


def process_folder(
    config: RunConfig,
    progress: Optional[ProgressCallback] = None,
    stop_event: Optional[threading.Event] = None,
) -> dict[str, Any]:
    api_key = prepare_run(config)
    images = discover_images(config.folder, config.max_images)
    ensure_daily_limit(config, count_api_eligible_images(images, config.max_file_mb))
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
        "keywords",
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
    return ImageResult(
        index=int(data.get("index") or fallback_index),
        filename=str(data.get("filename", "")),
        source_path=str(data.get("source_path", "")),
        status=str(data.get("status", "ok")),
        provider=str(data.get("provider", "")),
        model=str(data.get("model", "")),
        title=str(data.get("title", "")),
        description=str(data.get("description", "")),
        keywords=keywords if isinstance(keywords, list) else split_list(keywords),
        categories=categories if isinstance(categories, list) else split_list(categories),
        notes=str(data.get("notes", "")),
        copy_line=str(data.get("copy_line", "")),
        error=str(data.get("error", "")),
        thumbnail=str(data.get("thumbnail", "")),
    )


def reindex_results(results: list[ImageResult]) -> list[ImageResult]:
    limited = results[:MAX_IMAGES]
    for index, result in enumerate(limited, start=1):
        result.index = index
    return limited


def save_pending_results(results: list[ImageResult]) -> dict[str, Any]:
    limited = reindex_results(list(results))
    payload = {
        "created_at": dt.datetime.now().isoformat(timespec="seconds"),
        "max_results": MAX_IMAGES,
        "count": len(limited),
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


def load_pending_results() -> list[ImageResult]:
    if not PENDING_PATH.exists():
        return []
    payload = json.loads(PENDING_PATH.read_text(encoding="utf-8"))
    raw_results = payload.get("results", [])
    if not isinstance(raw_results, list):
        return []
    results = [
        result_from_dict(item, index)
        for index, item in enumerate(raw_results[:MAX_IMAGES], start=1)
        if isinstance(item, dict)
    ]
    return reindex_results(results)


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
        status_label = "完成" if result.status == "ok" else "錯誤"
        status_class = "ok" if result.status == "ok" else "error"
        rows.append(
            f"""
            <tr class="{status_class}">
              <td class="thumb"><img src="{html.escape(result.thumbnail, quote=True)}" alt=""></td>
              <td>
                <div class="filename">{html.escape(result.filename)}</div>
                <div class="muted">{html.escape(result.source_path)}</div>
              </td>
              <td><span class="status {status_class}">{status_label}</span></td>
              <td>
                <div class="title">{html.escape(result.title)}</div>
                <div class="description">{html.escape(result.description)}</div>
                <div class="notes">{html.escape(result.notes or result.error)}</div>
              </td>
              <td>
                <div class="keywords">{html.escape(keywords)}</div>
                <div class="muted">{html.escape(categories)}</div>
              </td>
              <td class="actions">
                <button data-copy="{html.escape(keywords, quote=True)}">複製關鍵字</button>
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
      grid-template-rows: auto minmax(260px, 1fr) minmax(160px, 0.8fr);
      gap: 12px;
      min-height: 0;
    }}
    .statusbar {{
      background: var(--panel);
      border: 1px solid var(--line);
      border-radius: 8px;
      padding: 14px;
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
    .tablewrap, .logwrap {{
      min-height: 0;
      overflow: auto;
      background: var(--panel);
      border: 1px solid var(--line);
      border-radius: 8px;
    }}
    .log {{
      white-space: pre-wrap;
      padding: 12px;
      font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
      font-size: 12px;
      line-height: 1.45;
    }}
    .ok {{ color: var(--ok); font-weight: 650; }}
    .error {{ color: var(--danger); font-weight: 650; }}
    @media (max-width: 960px) {{
      main {{ grid-template-columns: 1fr; height: auto; }}
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
        <input name="folder" value="{folder}" placeholder="/Users/你的帳號/Desktop/photos">
        <div class="hint">瀏覽器安全限制無法直接取得資料夾路徑，請從 Finder 複製路徑貼上。</div>
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
            <label>Model</label>
            <input name="model" value="{model}" list="modelSuggestions">
            <datalist id="modelSuggestions"></datalist>
          </div>
        </div>
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
        </div>
        <textarea name="prompt">{prompt}</textarea>
        <div class="hint">Prompt 會存到本機資料夾 ~/.stock_keyworder_prompts/。</div>
      </section>

      <section>
        <h2>4 執行</h2>
        <label><input name="watch" type="checkbox" style="width:auto" {watch_checked}> 監看資料夾</label>
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
          </div>
        </div>
        <progress id="progress" value="0" max="1"></progress>
      </div>
      <div class="tablewrap">
        <table>
          <thead><tr><th>檔名</th><th>狀態</th><th>Title</th><th>Description</th><th>Keywords</th><th>Notes</th><th>複製</th></tr></thead>
          <tbody id="results"></tbody>
        </table>
      </div>
      <div class="logwrap"><div id="log" class="log"></div></div>
    </div>
  </main>
  <script>
    const defaultPrompt = {json.dumps(DEFAULT_PROMPT, ensure_ascii=False)};

    const form = document.getElementById('settingsForm');
    const stateText = document.getElementById('stateText');
    const countText = document.getElementById('countText');
    const progress = document.getElementById('progress');
    const results = document.getElementById('results');
    const log = document.getElementById('log');
    const promptList = document.getElementById('promptList');
    const keyStatus = document.getElementById('keyStatus');
    const pendingStatus = document.getElementById('pendingStatus');
    const internalDefaults = {{
      max_images: {MAX_IMAGES},
      max_side: 1600,
      max_file_mb: {DEFAULT_MAX_FILE_MB},
      daily_limit: {DEFAULT_DAILY_API_LIMIT}
    }};
    const providerDefaults = {json.dumps(PROVIDER_DEFAULT_MODELS, ensure_ascii=False)};
    const providerModels = {json.dumps(PROVIDER_MODEL_SUGGESTIONS, ensure_ascii=False)};
    const knownProviders = Object.keys(providerDefaults);

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

    async function refresh() {{
      const status = await fetch('/api/status').then(r => r.json());
      stateText.textContent = status.state || '待命';
      countText.textContent = ' ' + (status.done || 0) + ' / ' + (status.total || 0);
      progress.max = Math.max(status.total || 1, 1);
      progress.value = status.done || 0;
      log.textContent = (status.logs || []).join('\\n');
      log.parentElement.scrollTop = log.parentElement.scrollHeight;
      results.innerHTML = (status.results || []).map(item => {{
        const keywords = (item.keywords || []).join(', ');
        const copyLine = item.copy_line || [item.title || '', item.description || '', keywords].join('\\t');
        return `
          <tr>
            <td>${{esc(item.filename)}}</td>
            <td class="${{item.status === 'ok' ? 'ok' : 'error'}}">${{esc(item.status)}}</td>
            <td>${{esc(item.title)}}</td>
            <td>${{esc(item.description)}}</td>
            <td>${{esc(keywords)}}</td>
            <td>${{esc(item.notes || item.error || '')}}</td>
            <td>
              <button type="button" data-copy="${{esc(keywords)}}">關鍵字</button>
              <button type="button" data-copy="${{esc(copyLine)}}">整列</button>
              <button type="button" data-delete-index="${{esc(item.index)}}" data-filename="${{esc(item.filename)}}">刪除</button>
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
        "manifest": {},
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
            return {
                "state": app_state["state"],
                "done": app_state["done"],
                "total": app_state["total"],
                "logs": list(app_state["logs"]),
                "results": [asdict(result) for result in app_state["results"]],
                "running": app_state["running"],
                "manifest": dict(app_state["manifest"]),
            }

    def set_state(**updates: Any) -> None:
        with state_lock:
            app_state.update(updates)

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
                daily_limit=int(payload.get("daily_limit", DEFAULT_DAILY_API_LIMIT)),
            ),
            bool(payload.get("watch", False)),
        )

    def run_job(config: RunConfig, watch_mode: bool, stop_event: threading.Event) -> None:
        def progress(kind: str, payload: Any) -> None:
            if kind == "scan":
                set_state(total=int(payload["total"]), done=0, state="執行中")
                add_log(f"找到/上限 {payload['total']} 張")
            elif kind == "log":
                add_log(redact_sensitive(payload, [config.api_key]))
            elif kind == "progress":
                set_state(done=int(payload["done"]), total=int(payload["total"]))
            elif kind == "result":
                with state_lock:
                    app_state["results"].append(payload)
            elif kind in {"done", "saved"}:
                set_state(manifest=payload, state="完成")
                if payload.get("html"):
                    add_log(f"報表：{payload.get('html', '')}")
                else:
                    add_log("完成，結果已顯示在右側表格。")

        try:
            if watch_mode:
                manifest = watch_folder(config, progress=progress, stop_event=stop_event)
            else:
                manifest = process_folder(config, progress=progress, stop_event=stop_event)
            set_state(manifest=manifest, state="完成")
        except Exception as exc:
            set_state(state="錯誤")
            add_log(f"錯誤：{redact_sensitive(exc, [config.api_key])}")
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
            self.send_json({"error": "Not found"}, 404)

        def do_POST(self) -> None:
            parsed = urllib.parse.urlparse(self.path)
            try:
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
                                "manifest": {},
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
                if parsed.path == "/api/clear-key":
                    payload = self.read_json()
                    provider = str(payload.get("provider", "openai")).strip().lower()
                    clear_cached_api_key(provider)
                    self.send_json({"ok": True})
                    return
                if parsed.path == "/api/save-progress":
                    with state_lock:
                        results_copy = list(app_state["results"])
                    self.send_json(save_pending_results(results_copy))
                    return
                if parsed.path == "/api/load-progress":
                    if not pending_results_status().get("exists"):
                        self.send_json({"error": "目前沒有已儲存進度。"}, 404)
                        return
                    with state_lock:
                        if app_state["running"]:
                            self.send_json({"error": "執行中不能載入進度，請先停止或等完成。"}, 409)
                            return
                    pending_results = load_pending_results()
                    with state_lock:
                        app_state["results"] = pending_results
                        app_state["done"] = len(pending_results)
                        app_state["total"] = len(pending_results)
                        app_state["state"] = "已載入進度"
                        app_state["manifest"] = {}
                    add_log(f"已載入 {len(pending_results)} 筆進度。")
                    self.send_json({"ok": True, "count": len(pending_results)})
                    return
                if parsed.path == "/api/delete-result":
                    payload = self.read_json()
                    target_index = int(payload.get("index", 0) or 0)
                    with state_lock:
                        current_results = list(app_state["results"])
                        remaining = [result for result in current_results if int(result.index) != target_index]
                        if len(remaining) == len(current_results):
                            self.send_json({"error": "找不到要刪除的結果。"}, 404)
                            return
                        app_state["results"] = reindex_results(remaining)
                        if not app_state["running"]:
                            app_state["done"] = len(app_state["results"])
                            app_state["total"] = len(app_state["results"])
                        results_copy = list(app_state["results"])
                    saved = save_pending_results(results_copy)
                    add_log(f"已刪除第 {target_index} 筆，剩餘 {len(results_copy)} 筆；進度已更新。")
                    self.send_json({"ok": True, "count": len(results_copy), "saved": saved})
                    return
                if parsed.path == "/api/stop":
                    with state_lock:
                        stop_event = app_state.get("stop_event")
                    if stop_event:
                        stop_event.set()
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

            ttk.Label(model_box, text="Model", style="Panel.TLabel").grid(row=1, column=0, sticky="w", pady=(8, 0))
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
            ttk.Button(copy_bar, text="複製關鍵字", command=self._copy_selected_keywords).grid(
                row=0, column=0, sticky="w", padx=(0, 8)
            )
            ttk.Button(copy_bar, text="複製整列", command=self._copy_selected_line).grid(
                row=0, column=1, sticky="w", padx=(0, 8)
            )
            ttk.Button(copy_bar, text="刪除該筆", command=self._delete_selected_result).grid(
                row=0, column=2, sticky="w", padx=(0, 8)
            )
            ttk.Button(copy_bar, text="儲存進度", command=self._save_progress).grid(
                row=0, column=3, sticky="w", padx=(0, 8)
            )
            ttk.Button(copy_bar, text="載入進度", command=self._load_progress).grid(
                row=0, column=4, sticky="w"
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
                ensure_daily_limit(config, 1 if watch_mode else planned_images)
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
                f"每張照片至少 1 次 API request；重試會額外消耗。\n"
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
            keywords = ", ".join(result.keywords or [])
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
            keywords = ", ".join(result.keywords or [])
            detail = "\n".join(
                [
                    f"Description: {result.description}",
                    "",
                    f"Keywords: {keywords}",
                    "",
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
    parser.add_argument("--retries", type=int, default=1)
    parser.add_argument("--daily-limit", type=int, default=DEFAULT_DAILY_API_LIMIT)
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
    )

    try:
        prepare_run(config)
        planned_images = (
            config.max_images
            if args.watch
            else count_api_eligible_images(discover_images(config.folder, config.max_images), config.max_file_mb)
        )
        ensure_daily_limit(config, 1 if args.watch else planned_images)
    except Exception as exc:
        print(redact_sensitive(exc, [config.api_key]), file=sys.stderr)
        return 2

    if (args.watch or planned_images >= CONFIRM_API_CALLS_THRESHOLD) and not args.yes:
        remaining = get_daily_remaining(config)
        message = (
            f"Provider/model: {config.provider} / {config.model}\n"
            f"Planned API images: {planned_images}\n"
            f"Max file size: {config.max_file_mb} MB; larger files are skipped without API calls.\n"
            f"Each image uses at least 1 API request; retries use more.\n"
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
