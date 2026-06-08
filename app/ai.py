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
  "assignment_name": "краткое название назначения или пусто",
  "assignment_date": "YYYY-MM-DD или пусто",
  "doctor": "врач/специализация или пусто",
  "comment": "общий комментарий к назначению или пусто",
  "warnings": ["что родителю проверить вручную"],
  "medicines": [
    {
      "name": "название препарата или пусто",
      "dose": "дозировка или пусто",
      "dosage_form": "форма выпуска: таблетки/капсулы/мл/пакеты или пусто",
      "administration_route": "способ применения: внутрь/развести в жидкости/другое или пусто",
      "analogs": ["аналогичные названия препарата, если перечислены в назначении"],
      "frequency_count": 1,
      "frequency_unit": "day|week|2weeks|month|custom",
      "timing_template": "fixed|before_meal|with_meal|after_meal",
      "meals": ["breakfast","lunch","dinner"],
      "times": ["HH:MM"],
      "comment": "комментарий",
      "duration_value": 14,
      "duration_unit": "days|weeks|months или пусто",
      "start_date": "YYYY-MM-DD или пусто",
      "end_date": "YYYY-MM-DD или пусто"
    }
  ]
}
Если частота 3 раза в день и привязка к еде, meals обычно breakfast/lunch/dinner. Для 2 раз в день — breakfast/dinner, если явно не указано иное.
Если указано «до еды» — before_meal, «во время еды» — with_meal, «после еды» — after_meal.
Если в назначении несколько препаратов, верни их все в medicines. Не выбирай один препарат вместо списка.

Правила интерпретации дозировки и частоты:
- Если написано «6 таб в день», «6 таблеток в день», «6 раз в день», это НЕ одна доза 6 таблеток. Верни dose = "1 таб", frequency_count = 6, frequency_unit = "day", если явно не сказано «6 таблеток за один прием».
- Если написано «по 1 таб 3 раза в день», dose = "1 таб", frequency_count = 3.
- Если написано «2 дозы в каждую половину носа 3 раза в день», dose = "2 дозы в каждую половину носа", frequency_count = 3, administration_route = "в нос".
- Если написано «на ночь», timing_template = "fixed", times = ["21:00"], comment = "на ночь".
- Если написано «10 дней», «14 дней», «1 мес», заполни duration_value/duration_unit.
- Не объединяй несколько препаратов и не отбрасывай строки: каждая строка назначения должна стать отдельным элементом medicines.
- Если строка выглядит как процедура/применение без явной дозы (например: «полоскать горло р-ром ОКИ 2 раза в день, 5 дней»), все равно верни отдельный препарат: name = "ОКИ", dose = "1 применение" или "по инструкции", dosage_form = "раствор", administration_route = "полоскать горло", frequency_count = 2, duration_value = 5, duration_unit = "days".
- Если препарат написан строчными буквами, верни название с большой буквы; устоявшиеся аббревиатуры вроде «ОКИ» оставь заглавными.
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
