"""双层合规审核：本地敏感词（第一层） + 大模型语义校验（第二层）。"""

import json
import os
import re
from pathlib import Path

from .prompts import COMPLIANCE_SYSTEM

_WORDS_FILE = Path(__file__).parent / "data" / "sensitive_words.txt"


def _load_words() -> list[str]:
    words = []
    for line in _WORDS_FILE.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#"):
            words.append(line)
    return words


SENSITIVE_WORDS = _load_words()


def local_check(text: str) -> list[str]:
    """第一层：本地敏感词匹配，返回命中的词。"""
    return [w for w in SENSITIVE_WORDS if w in text]


def llm_check(client, model: str, text: str) -> dict:
    """第二层：大模型语义校验（Gemini JSON 模式）。返回 {level, issues, suggestion}。"""
    if os.environ.get("YD_LLM_COMPLIANCE", "1") != "1":
        return {"level": "pass", "issues": [], "suggestion": "", "skipped": True}
    try:
        from google.genai import types as gtypes
        resp = client.models.generate_content(
            model=model,
            contents=f"待审核文本：\n{text[:6000]}",
            config=gtypes.GenerateContentConfig(
                system_instruction=COMPLIANCE_SYSTEM,
                response_mime_type="application/json",
                max_output_tokens=2000,
            ),
        )
        raw = resp.text or "{}"
        m = re.search(r"\{.*\}", raw, re.S)
        result = json.loads(m.group(0)) if m else {}
        if result.get("level") not in ("pass", "caution", "block"):
            result = {"level": "caution", "issues": ["审核结果解析失败"], "suggestion": "建议人工复核"}
        result.setdefault("issues", [])
        result.setdefault("suggestion", "")
        return result
    except Exception as e:  # 审核服务异常时降级为提示人工复核，不阻塞主流程
        return {"level": "caution", "issues": [f"合规服务异常：{type(e).__name__}"],
                "suggestion": "建议人工复核后发布"}
