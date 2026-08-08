from __future__ import annotations

import json
from pathlib import Path


def patch_model_index() -> None:
    path = Path("app/db/models.py")
    text = path.read_text()
    marker = '"uq_model_sources_subscription_fallback"'
    if marker in text:
        return
    old = 'class ModelSource(Base):\n    __tablename__ = "model_sources"\n\n'
    new = """class ModelSource(Base):
    __tablename__ = "model_sources"
    __table_args__ = (
        Index(
            "uq_model_sources_subscription_fallback",
            "is_subscription_fallback",
            unique=True,
            postgresql_where=text("is_subscription_fallback IS TRUE"),
            sqlite_where=text("is_subscription_fallback = 1"),
        ),
    )

"""
    if text.count(old) != 1:
        raise RuntimeError("ModelSource class anchor changed")
    path.write_text(text.replace(old, new, 1))


def append_locale_entries(filename: str, entries: dict[str, str]) -> None:
    path = Path("frontend/src/i18n/locales") / filename
    text = path.read_text()
    parsed = json.loads(text)
    missing = {key: value for key, value in entries.items() if key not in parsed}
    if not missing:
        return
    if not text.endswith("}\n"):
        raise RuntimeError(f"Unexpected locale formatting: {path}")
    prefix = text[:-2].rstrip()
    additions = ",\n" + ",\n".join(
        f"  {json.dumps(key, ensure_ascii=False)}: {json.dumps(value, ensure_ascii=False)}"
        for key, value in missing.items()
    )
    updated = prefix + additions + "\n}\n"
    json.loads(updated)
    path.write_text(updated)


def main() -> None:
    patch_model_index()
    append_locale_entries(
        "en.json",
        {
            "modelSources.fields.subscriptionFallback": "Use as subscription fallback",
            "modelSources.fields.subscriptionFallbackDescription": (
                "Used only after all eligible ChatGPT accounts report upstream usage exhaustion."
            ),
            "modelSources.fields.fallbackModelOverride": "Fallback model override (optional)",
            "modelSources.fields.fallbackModelPlaceholder": "Leave blank to preserve the requested model",
        },
    )
    append_locale_entries(
        "ko.json",
        {
            "modelSources.fields.subscriptionFallback": "구독 폴백으로 사용",
            "modelSources.fields.subscriptionFallbackDescription": (
                "적격한 모든 ChatGPT 계정이 업스트림 사용량 한도에 도달한 경우에만 사용됩니다."
            ),
            "modelSources.fields.fallbackModelOverride": "폴백 모델 재정의(선택 사항)",
            "modelSources.fields.fallbackModelPlaceholder": "요청된 모델을 유지하려면 비워 두세요",
        },
    )
    append_locale_entries(
        "zh-CN.json",
        {
            "modelSources.fields.subscriptionFallback": "用作订阅回退",
            "modelSources.fields.subscriptionFallbackDescription": (
                "仅当所有符合条件的 ChatGPT 账户都达到上游用量限制时使用。"
            ),
            "modelSources.fields.fallbackModelOverride": "回退模型覆盖（可选）",
            "modelSources.fields.fallbackModelPlaceholder": "留空以保留请求的模型",
        },
    )


if __name__ == "__main__":
    main()
