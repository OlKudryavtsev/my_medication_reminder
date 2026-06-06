from __future__ import annotations

import base64
import json
import re
from dataclasses import dataclass
from typing import Any

from openai import AsyncOpenAI

from .config import get_settings

settings = get_settings()


class AIUnavailable(RuntimeError):
    pass


def ai_enabled() -> bool:
    return bool(getattr(settings, "openai_enabled", False) and getattr(settings, "openai_api_key", "").strip())


def _client() -> AsyncOpenAI:
    if not ai_enabled():
        raise AIUnavailable("OpenAI выключен или не задан OPENAI_API_KEY")
    return AsyncOpenAI(api_key=settings.openai_api_key.strip())


def _extract_json(text: str) -> dict[str, Any]:
    text = (text or "").strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    try:
        return json.loads(text)
    except Exception:
        m = re.search(r"\{.*\}", text, re.S)
        if not m:
            raise
        return json.loads(m.group(0))


async def ask_json(prompt: str, user_text: str = "", image_bytes: bytes | None = None, content_type: str = "image/jpeg") -> dict[str, Any]:
    client = _client()
    model = settings.openai_model.strip() or "gpt-4o-mini"
    content: list[dict[str, Any]] = []
    if user_text:
        content.append({"type": "text", "text": user_text})
    if image_bytes:
        b64 = base64.b64encode(image_bytes).decode("ascii")
        content.append({"type": "image_url", "image_url": {"url": f"data:{content_type};base64,{b64}"}})
    if not content:
        content.append({"type": "text", "text": ""})

    resp = await client.chat.completions.create(
        model=model,
        temperature=0.1,
        messages=[
            {"role": "system", "content": prompt},
            {"role": "user", "content": content},
        ],
    )
    return _extract_json(resp.choices[0].message.content or "{}")


MEDICINE_SCHEMA_PROMPT = """
Ты помощник семейного трекера приема лекарств. Твоя задача — извлечь данные из текста назначения.
Не давай медицинских советов, не меняй дозировку, не придумывай отсутствующие данные.
Верни только JSON без Markdown.
Схема:
{
  "ok": true,
  "warnings": ["что родителю проверить вручную"],
  "medicines": [
    {
      "name": "название препарата или пусто",
      "dose": "дозировка или пусто",
      "frequency_count": 1,
      "frequency_unit": "day|week|2weeks|month|custom",
      "timing_template": "fixed|before_meal|with_meal|after_meal",
      "meals": ["breakfast","lunch","dinner"],
      "times": ["HH:MM"],
      "comment": "комментарий",
      "start_date": "YYYY-MM-DD или пусто",
      "end_date": "YYYY-MM-DD или пусто"
    }
  ]
}
Если частота 3 раза в день и привязка к еде, meals обычно breakfast/lunch/dinner. Для 2 раз в день — breakfast/dinner, если явно не указано иное.
Если указано «до еды» — before_meal, «во время еды» — with_meal, «после еды» — after_meal.
"""


PRESCRIPTION_IMAGE_PROMPT = """
Ты аккуратно распознаешь фото или файл назначения врача для семейного трекера лекарств.
Извлекай только то, что реально видно/написано. Не назначай лечение и не исправляй врача.
Верни только JSON без Markdown по схеме из system ниже.
Если не уверен — добавь предупреждение в warnings.
""" + MEDICINE_SCHEMA_PROMPT


INVENTORY_PHOTO_PROMPT = """
Ты распознаешь фото упаковки лекарства для домашней аптечки. Не давай медицинских советов.
Верни только JSON без Markdown:
{
  "ok": true,
  "name": "название препарата или пусто",
  "unit_name": "таб|капс|мл|пакет|шт или шт",
  "warnings": ["что проверить вручную"]
}
"""


REPORT_PROMPT = """
Ты составляешь черновик отчета для врача на основе фактической статистики приема лекарств.
Не делай медицинских выводов и рекомендаций по лечению. Пиши кратко, структурировано, по-русски.
Верни только JSON без Markdown:
{
  "ok": true,
  "title": "Отчет о соблюдении назначений",
  "summary": "2-5 предложений",
  "bullets": ["ключевые факты"],
  "questions_for_doctor": ["что стоит уточнить у врача"]
}
"""
