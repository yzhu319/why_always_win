"""赢币 + 账单：SQLite 最简实现（V1.0 alpha，匿名 cookie 用户）。"""

import os
import sqlite3
import uuid
from datetime import date, datetime, timedelta
from pathlib import Path

# Vercel 等 serverless 环境文件系统只读，仅 /tmp 可写（数据为临时的，alpha 可接受；
# 二期迁移 Vercel KV/Postgres）。本地默认存 data/app.db。
if os.environ.get("YD_DB"):
    DB_PATH = Path(os.environ["YD_DB"])
elif os.environ.get("VERCEL"):
    DB_PATH = Path("/tmp/yd_app.db")
else:
    DB_PATH = Path(__file__).parent.parent / "data" / "app.db"

NEW_USER_COINS = 10   # 新用户赠送
DAILY_COINS = 3       # 每日登录赠送
COST_PER_GEN = 1      # 每次完整研判消耗

PLANS = {
    "pack50": {"name": "9.9元 / 50赢币", "coins": 50, "price": 9.9},
    "monthly": {"name": "19.9元 / 月度无限赢币", "coins": 0, "price": 19.9, "days": 30},
}


def _conn():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    with _conn() as c:
        c.execute("""CREATE TABLE IF NOT EXISTS users (
            id TEXT PRIMARY KEY,
            coins INTEGER NOT NULL,
            member_until TEXT,
            last_daily TEXT,
            created_at TEXT NOT NULL
        )""")
        c.execute("""CREATE TABLE IF NOT EXISTS ledger (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id TEXT NOT NULL,
            delta INTEGER NOT NULL,
            reason TEXT NOT NULL,
            ts TEXT NOT NULL
        )""")


def _log(c, user_id: str, delta: int, reason: str):
    c.execute("INSERT INTO ledger (user_id, delta, reason, ts) VALUES (?,?,?,?)",
              (user_id, delta, reason, datetime.now().isoformat(timespec="seconds")))


def get_or_create_user(user_id: str | None) -> dict:
    """取用户；不存在则创建并发新人赢币；每天首次访问发每日赢币。"""
    today = date.today().isoformat()
    with _conn() as c:
        row = c.execute("SELECT * FROM users WHERE id=?", (user_id,)).fetchone() if user_id else None
        if row is None:
            user_id = user_id or uuid.uuid4().hex
            c.execute("INSERT INTO users (id, coins, last_daily, created_at) VALUES (?,?,?,?)",
                      (user_id, NEW_USER_COINS, today, datetime.now().isoformat(timespec="seconds")))
            _log(c, user_id, NEW_USER_COINS, "新用户赠送")
            row = c.execute("SELECT * FROM users WHERE id=?", (user_id,)).fetchone()
        elif row["last_daily"] != today:
            c.execute("UPDATE users SET coins=coins+?, last_daily=? WHERE id=?",
                      (DAILY_COINS, today, user_id))
            _log(c, user_id, DAILY_COINS, "每日登录赠送")
            row = c.execute("SELECT * FROM users WHERE id=?", (user_id,)).fetchone()
        return dict(row)


def is_member(user: dict) -> bool:
    mu = user.get("member_until")
    return bool(mu) and mu >= date.today().isoformat()


def try_spend(user_id: str) -> tuple[bool, dict]:
    """扣一次生成费。会员不扣。返回 (是否成功, 最新用户)。"""
    with _conn() as c:
        row = c.execute("SELECT * FROM users WHERE id=?", (user_id,)).fetchone()
        if row is None:
            return False, {}
        user = dict(row)
        if is_member(user):
            return True, user
        if user["coins"] < COST_PER_GEN:
            return False, user
        c.execute("UPDATE users SET coins=coins-? WHERE id=?", (COST_PER_GEN, user_id))
        _log(c, user_id, -COST_PER_GEN, "赢面研判消耗")
        user["coins"] -= COST_PER_GEN
        return True, user


def refund(user_id: str):
    """生成失败时退回赢币（会员本来就没扣，重复退无副作用之外要避免——只在扣费成功且非会员时调用）。"""
    with _conn() as c:
        c.execute("UPDATE users SET coins=coins+? WHERE id=?", (COST_PER_GEN, user_id))
        _log(c, user_id, COST_PER_GEN, "生成失败退回")


def recharge(user_id: str, plan_id: str) -> dict:
    """V1.0 alpha：mock 充值，不接支付，直接到账并记账。"""
    plan = PLANS[plan_id]
    with _conn() as c:
        if plan_id == "monthly":
            row = c.execute("SELECT member_until FROM users WHERE id=?", (user_id,)).fetchone()
            base = date.today()
            if row and row["member_until"] and row["member_until"] >= base.isoformat():
                base = date.fromisoformat(row["member_until"])
            until = (base + timedelta(days=plan["days"])).isoformat()
            c.execute("UPDATE users SET member_until=? WHERE id=?", (until, user_id))
            _log(c, user_id, 0, f"充值[mock]：{plan['name']}")
        else:
            c.execute("UPDATE users SET coins=coins+? WHERE id=?", (plan["coins"], user_id))
            _log(c, user_id, plan["coins"], f"充值[mock]：{plan['name']}")
        return dict(c.execute("SELECT * FROM users WHERE id=?", (user_id,)).fetchone())


def billing(user_id: str, limit: int = 50) -> list[dict]:
    with _conn() as c:
        rows = c.execute(
            "SELECT delta, reason, ts FROM ledger WHERE user_id=? ORDER BY id DESC LIMIT ?",
            (user_id, limit)).fetchall()
        return [dict(r) for r in rows]
