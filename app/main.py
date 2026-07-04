"""赢典AI V1.0 — FastAPI 后端。

运行：uvicorn app.main:app --port 8000
"""

import json
import os
from pathlib import Path

from fastapi import FastAPI, Request, Response
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from google import genai
from google.genai import types as gtypes
from pydantic import BaseModel

from . import compliance, db, prompts, trending

MODEL = os.environ.get("YD_MODEL", "gemini-2.5-flash")
STATIC_DIR = Path(__file__).parent.parent / "static"
COOKIE = "yd_uid"

app = FastAPI(title="赢典AI", version="0.1.0")
client = genai.Client()  # 读取 GEMINI_API_KEY 环境变量

db.init_db()


def _user_from(request: Request) -> dict:
    return db.get_or_create_user(request.cookies.get(COOKIE))


def _attach_cookie(resp: Response, user: dict):
    resp.set_cookie(COOKIE, user["id"], max_age=3600 * 24 * 365, httponly=True)


def _user_payload(user: dict) -> dict:
    return {
        "id": user["id"][:8],
        "coins": user["coins"],
        "is_member": db.is_member(user),
        "member_until": user.get("member_until"),
    }


@app.get("/")
async def index():
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/api/me")
async def me(request: Request):
    user = _user_from(request)
    resp = JSONResponse(_user_payload(user))
    _attach_cookie(resp, user)
    return resp


class RechargeReq(BaseModel):
    plan: str


@app.post("/api/recharge")
async def recharge(request: Request, body: RechargeReq):
    user = _user_from(request)
    if body.plan not in db.PLANS:
        return JSONResponse({"error": "未知档位"}, status_code=400)
    user = db.recharge(user["id"], body.plan)
    resp = JSONResponse({"ok": True, "user": _user_payload(user),
                         "note": "alpha 内测：模拟充值已到账，未接入真实支付"})
    _attach_cookie(resp, user)
    return resp


@app.get("/api/billing")
async def billing(request: Request):
    user = _user_from(request)
    return JSONResponse({"records": db.billing(user["id"])})


@app.get("/api/trending")
async def api_trending():
    items = await trending.get_trending()
    return JSONResponse({"items": items})


class GenerateReq(BaseModel):
    mode: str = "chat"                 # chat | copywriting | comment | analysis | trending
    messages: list[dict] = []          # [{role, content}] 历史（含本次用户输入）
    style: str = "rational"
    platform: str = "general"
    intensity: str = "objective"


def _sse(payload: dict) -> str:
    return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"


@app.post("/api/generate")
async def generate(request: Request, body: GenerateReq):
    user = _user_from(request)

    # 输入侧第一层敏感词拦截
    last_input = ""
    for m in reversed(body.messages):
        if m.get("role") == "user":
            last_input = str(m.get("content", ""))
            break
    hits = compliance.local_check(last_input)
    if hits:
        return JSONResponse(
            {"error": f"输入包含违规内容（{'、'.join(hits)}），已拦截。"}, status_code=400)

    # 赢币扣费
    ok, user = db.try_spend(user["id"])
    if not ok:
        return JSONResponse(
            {"error": "赢币不足，需补充赢力继续立论", "coins": user.get("coins", 0)},
            status_code=402)
    charged = not db.is_member(user)

    system = prompts.build_system(body.mode, body.style, body.platform, body.intensity)
    contents = [
        {"role": "model" if m["role"] == "assistant" else "user",
         "parts": [{"text": str(m.get("content", ""))}]}
        for m in body.messages if m.get("role") in ("user", "assistant")
    ]

    async def stream():
        full_text = []
        try:
            gen = await client.aio.models.generate_content_stream(
                model=MODEL,
                contents=contents,
                config=gtypes.GenerateContentConfig(
                    system_instruction=system,
                    max_output_tokens=8000,
                ),
            )
            async for chunk in gen:
                if chunk.text:
                    full_text.append(chunk.text)
                    yield _sse({"type": "delta", "text": chunk.text})
        except Exception as e:
            if charged:
                db.refund(user["id"])
            yield _sse({"type": "error", "error": f"生成失败：{type(e).__name__}，赢币已退回"})
            return

        output = "".join(full_text)

        # 输出侧双层合规审核
        out_hits = compliance.local_check(output)
        review = compliance.llm_check(client, MODEL, output)
        if out_hits:
            review["level"] = "block"
            review["issues"] = review.get("issues", []) + [f"命中敏感词：{'、'.join(out_hits)}"]

        latest = db.get_or_create_user(user["id"])
        yield _sse({"type": "done", "compliance": review, "user": _user_payload(latest)})

    resp = StreamingResponse(stream(), media_type="text/event-stream")
    resp.headers["Cache-Control"] = "no-cache"
    resp.headers["X-Accel-Buffering"] = "no"
    _attach_cookie(resp, user)
    return resp


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=int(os.environ.get("YD_PORT", 8000)))
