from __future__ import annotations

import json
import os
from datetime import datetime
from typing import Any, Dict, List, Optional
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import httpx
from dotenv import load_dotenv
from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.responses import JSONResponse

load_dotenv()

app = FastAPI(title="Reminder Parser API")

DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY", "").strip()
DEEPSEEK_BASE_URL = os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com").strip()
DEEPSEEK_MODEL = os.getenv("DEEPSEEK_MODEL", "deepseek-v4-pro").strip()
API_KEY = os.getenv("API_KEY", "").strip()
DEFAULT_TZ = os.getenv("DEFAULT_TZ", "Asia/Shanghai").strip()
DISABLE_THINKING = os.getenv("DEEPSEEK_DISABLE_THINKING", "true").strip().lower() in {
    "1",
    "true",
    "yes",
}

SYSTEM_PROMPT = """你是日程/待办解析助手。根据输入文本提取核心信息并输出严格 JSON。
请遵守：
1) 仅返回 JSON 对象，不要输出解释或多余文本。
2) 若信息不足，返回 ok=false, need_confirm=true，并给出简短 question，items 为空。
3) items 仅包含 type 为 calendar 或 reminder。
4) calendar 必须包含 title, start, end（ISO 8601 含时区），end 晚于 start。
5) reminder 必须包含 title, due（ISO 8601 含时区）。
6) title 不能为空；location、notes、alert_minutes 可选。
7) 默认最多 3 个 items。
8) 长通知提取核心事件/任务，不要机械复述全文。
9) 多个任务共享同一截止时间时，优先合并为一个 reminder，细节放入 notes。
10) 不确定时不要臆造，改为 need_confirm。
输出格式示例：
{
  "ok": true,
  "need_confirm": false,
  "question": "",
  "items": [
    {
      "type": "calendar",
      "title": "毕业论文答辩",
      "start": "2026-05-17T08:30:00+08:00",
      "end": "2026-05-17T09:30:00+08:00",
      "location": "教五",
      "notes": "8:00到教室拷贝PPT；携带纸质论文和答辩评分表。",
      "alert_minutes": 30
    }
  ]
}"""


async def verify_api_key(x_api_key: str = Header(None)):
    if not API_KEY:
        return None
    if not x_api_key:
        raise HTTPException(status_code=401, detail="Missing X-API-Key header")
    if x_api_key != API_KEY:
        raise HTTPException(status_code=403, detail="Invalid API key")
    return x_api_key


def error_response(message: str) -> JSONResponse:
    return JSONResponse(
        {
            "ok": False,
            "need_confirm": True,
            "question": message,
            "items": [],
        }
    )


def parse_iso_datetime(value: Any) -> Optional[datetime]:
    if not isinstance(value, str):
        return None
    text = value.strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return None
    return parsed


def normalize_items(raw_items: Any) -> Optional[List[Dict[str, Any]]]:
    if not isinstance(raw_items, list):
        return None
    cleaned: List[Dict[str, Any]] = []
    for item in raw_items[:3]:
        if not isinstance(item, dict):
            return None
        item_type = str(item.get("type", "")).strip()
        title = str(item.get("title", "")).strip()
        if item_type not in {"calendar", "reminder"}:
            return None
        if not title:
            return None

        result: Dict[str, Any] = {"type": item_type, "title": title}
        if item_type == "calendar":
            start = parse_iso_datetime(item.get("start"))
            end = parse_iso_datetime(item.get("end"))
            if start is None or end is None or end <= start:
                return None
            result["start"] = item["start"]
            result["end"] = item["end"]
            location = item.get("location")
            notes = item.get("notes")
            if isinstance(location, str) and location.strip():
                result["location"] = location.strip()
            if isinstance(notes, str) and notes.strip():
                result["notes"] = notes.strip()
        else:
            due = parse_iso_datetime(item.get("due"))
            if due is None:
                return None
            result["due"] = item["due"]
            notes = item.get("notes")
            if isinstance(notes, str) and notes.strip():
                result["notes"] = notes.strip()

        alert_minutes = item.get("alert_minutes")
        if alert_minutes is not None:
            if isinstance(alert_minutes, bool) or not isinstance(alert_minutes, int):
                return None
            if alert_minutes < 0:
                return None
            result["alert_minutes"] = alert_minutes

        cleaned.append(result)

    return cleaned


def normalize_response(raw: Any) -> Optional[Dict[str, Any]]:
    if not isinstance(raw, dict):
        return None
    if raw.get("ok") is False or raw.get("need_confirm") is True:
        question = raw.get("question") or raw.get("error") or "需要补充关键信息。"
        return {
            "ok": False,
            "need_confirm": True,
            "question": str(question),
            "items": [],
        }
    items = normalize_items(raw.get("items"))
    if items is None or not items:
        return None
    return {
        "ok": True,
        "need_confirm": False,
        "question": "",
        "items": items,
    }


async def extract_text(request: Request) -> str:
    content_type = request.headers.get("content-type", "")
    if "application/json" in content_type:
        try:
            data = await request.json()
        except json.JSONDecodeError:
            return ""
        if isinstance(data, dict):
            return str(data.get("text", "")).strip()
        return ""
    body = await request.body()
    text = body.decode("utf-8", errors="replace").strip()
    if text:
        return text
    try:
        data = json.loads(body.decode("utf-8"))
    except json.JSONDecodeError:
        return ""
    return str(data.get("text", "")).strip()


def build_payload(text: str, now: datetime, timezone: str) -> Dict[str, Any]:
    user_content = f"CURRENT_TIME: {now.isoformat()}\nTIMEZONE: {timezone}\nTEXT:\n{text}"
    payload: Dict[str, Any] = {
        "model": DEEPSEEK_MODEL,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_content},
        ],
        "response_format": {"type": "json_object"},
        "temperature": 0.2,
        "stream": False,
    }
    if DISABLE_THINKING:
        payload["thinking"] = {"type": "disabled"}
    return payload


async def call_deepseek(text: str, now: datetime, timezone: str) -> Any:
    if not DEEPSEEK_API_KEY:
        return {"error": "Missing DEEPSEEK_API_KEY"}
    payload = build_payload(text, now, timezone)
    headers = {"Authorization": f"Bearer {DEEPSEEK_API_KEY}"}
    async with httpx.AsyncClient(base_url=DEEPSEEK_BASE_URL, timeout=30) as client:
        response = await client.post("/chat/completions", json=payload, headers=headers)
    if response.status_code >= 400:
        return {"error": f"DeepSeek API error: {response.status_code}"}
    return response.json()


def extract_model_json(api_response: Any) -> Optional[Dict[str, Any]]:
    if not isinstance(api_response, dict):
        return None
    choices = api_response.get("choices")
    if not isinstance(choices, list) or not choices:
        return None
    message = choices[0].get("message")
    if not isinstance(message, dict):
        return None
    content = message.get("content")
    if not isinstance(content, str):
        return None
    try:
        return json.loads(content)
    except json.JSONDecodeError:
        return None


@app.post("/parse")
async def parse_text(request: Request, _auth: str = Depends(verify_api_key)) -> JSONResponse:
    text = await extract_text(request)
    if not text:
        return error_response("请输入要解析的文本。")
    try:
        tz = ZoneInfo(DEFAULT_TZ)
    except ZoneInfoNotFoundError:
        return error_response("服务器时区配置无效。")
    now = datetime.now(tz)

    try:
        api_response = await call_deepseek(text, now, DEFAULT_TZ)
    except httpx.HTTPError:
        return error_response("解析服务暂时不可用，请稍后再试。")
    if isinstance(api_response, dict) and "error" in api_response:
        return error_response(str(api_response["error"]))

    model_json = extract_model_json(api_response)
    normalized = normalize_response(model_json)
    if normalized is None:
        return error_response("模型返回格式不正确，需要补充信息或重试。")
    return JSONResponse(normalized)
