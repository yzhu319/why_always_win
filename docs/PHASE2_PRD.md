# 赢典AI Phase-2 PRD：赢币持久化存储

> 状态：Draft → 实施中（2026-07-04）
> 负责人：typora / 典典团队
> 前置：V1.3（去政治化选题 + 立场锁定 + 一键风格）已上线 whyalwayswin.vercel.app

## 1. 背景与问题

当前 V1 的赢币（coins）、会员（member_until）、账单流水（ledger）全部存 SQLite：

- 本地开发：`data/app.db`（持久，正常）。
- **Vercel 生产：`/tmp/yd_app.db`**——serverless 只有 `/tmp` 可写，且 `/tmp` 随实例回收清空。

导致的实际问题：
1. **余额会凭空重置**：函数冷启动/换实例后，用户赢币回到「新用户 10 币」或直接丢失，账单清零。
2. **多实例不一致**：Vercel 会并发拉起多个函数实例，各自持有一份 `/tmp` 数据库，同一用户在不同实例上余额不同。
3. **充值无意义**：即便接入真实支付，到账数据也会丢，无法进入商业化。

Phase-2 的唯一目标：**让赢币/会员/账单数据在生产环境持久、且跨实例一致**，为后续真实支付打地基。

## 2. 目标 / 非目标

### 目标
- G1 生产数据持久化：余额、会员有效期、账单流水掉电不丢、冷启动不重置。
- G2 跨实例强一致：任一实例读到的余额相同；并发扣费不超扣、不透支。
- G3 零改动上层：`main.py` 及前端不感知存储实现，`db.py` 公共 API 签名不变。
- G4 本地开发零门槛：不配置任何外部服务时，自动回落到本地 SQLite。
- G5 可回滚：仅靠一个环境变量切换新旧存储，出问题即时切回。

### 非目标（留给 Phase-3+）
- 真实支付 / 微信支付宝对接（本期仍为 mock 充值）。
- 账号体系（手机号/微信登录）——仍用匿名 cookie `yd_uid`。
- 数据分析看板、后台运营系统。
- 连接池/读写分离等规模化优化（低频 alpha 暂不需要）。

## 3. 方案选型

| 方案 | 契合度 | 结论 |
|------|--------|------|
| **Serverless Postgres**（Vercel Postgres＝Neon / Supabase） | 关系型，与现有 users+ledger 表天然吻合；支持 `RETURNING`/`ON CONFLICT` 做原子扣费 | ✅ **选用** |
| Vercel KV（Upstash Redis） | 键值型，需把关系数据拍平重建；原子扣费要 Lua/事务改造 | ✗ 改造大、账单流水不适合 |
| Turso（libSQL 云 SQLite） | 语义最接近，改动最小 | ⚪ 备选，但生态/额度不如 PG 通用 |

**决策**：采用 **Postgres**，通过标准连接串 `DATABASE_URL` 接入，驱动 `psycopg[binary]`（提供预编译 wheel，Vercel Python 构建可直接安装）。

## 4. 架构设计

### 4.1 存储抽象（单文件、双后端）
`app/db.py` 保持对外 API 不变，内部按环境变量选择后端：

```
DATABASE_URL 存在  → Postgres（psycopg3）      # 生产
否则              → SQLite（data/app.db）      # 本地开发
```

- 统一走 `cursor` 接口；占位符用 `?` 编写，PG 下自动转 `%s`。
- SQLite 3.35+ 与 Postgres 均支持 `RETURNING` 和 `ON CONFLICT`，绝大多数 SQL 一份通用，仅建表的自增主键 DDL 分叉。
- 连接策略：每请求开/关连接（低频 alpha 足够）；连接串建议用 Neon 的 **pooled** 端点以适配 serverless 短连接。

### 4.2 数据模型（不变）
```
users(  id TEXT PK, coins INT, member_until TEXT, last_daily TEXT, created_at TEXT )
ledger( id 自增PK, user_id TEXT, delta INT, reason TEXT, ts TEXT )
```
时间统一存 ISO 文本，避免两库类型差异。

### 4.3 并发正确性（本期核心升级）
现有 `try_spend` 是「先读后写」，并发下会超扣/透支。改为**原子条件更新**：

```sql
UPDATE users SET coins = coins - :cost
WHERE id = :id AND coins >= :cost
RETURNING coins;
```
- 返回行 → 扣费成功，用 `RETURNING` 的新余额。
- 无返回行 → 余额不足，明确拒绝。

同理，用户创建与每日赠币也改为原子写，避免并发重复发币：
- 创建：`INSERT ... ON CONFLICT (id) DO NOTHING RETURNING id`，仅当真正插入时记「新用户赠送」。
- 每日：`UPDATE ... SET coins=coins+:daily, last_daily=:today WHERE id=:id AND last_daily<>:today RETURNING coins`，仅当返回行时记「每日登录赠送」。

## 5. 配置与部署

| 环境变量 | 作用 | 生产取值 |
|----------|------|----------|
| `DATABASE_URL` | Postgres 连接串（设置即启用 PG） | Neon/Supabase pooled 连接串 |
| `YD_DB` | 本地 SQLite 路径覆盖（可选） | 不设 |

部署步骤（需用户在控制台操作账号相关部分）：
1. Vercel 项目 → Storage → Create → **Postgres（Neon）**，或自建 Neon/Supabase 免费实例。
2. 将其 `DATABASE_URL`（pooled）加入 Vercel 项目的 Production + Preview 环境变量。
3. `requirements.txt` 已含 `psycopg[binary]`，`vercel deploy --prod` 即生效。
4. 首次请求自动建表（`init_db()` 幂等）。

## 6. 迁移与回滚
- **迁移**：新库为空即可，`init_db()` 自动建表；旧 `/tmp` 数据本就是临时的，无需搬迁。老用户 cookie 命中空库 → 视为新用户重新发放（alpha 可接受）。
- **回滚**：删除/清空 `DATABASE_URL` 环境变量并重新部署，立即回落 SQLite（数据仍临时，仅用于止血）。

## 7. 验收标准
- AC1 设 `DATABASE_URL` 后，连续两次冷启动（或重启进程）间，余额/会员/账单保持不变。
- AC2 `DATABASE_URL` 与本地 SQLite 两种后端，全部 API（me/generate 扣费/recharge/billing）行为一致。
- AC3 并发 N 次扣费，扣减总额 = min(N, 起始余额)，绝不透支为负。
- AC4 未配置 `DATABASE_URL` 时本地开发照常跑，无需安装 psycopg。
- AC5 生成失败退款、每日赠币、mock 充值在 PG 后端全部正确记账。

## 8. 风险
- Neon 免费实例闲置会休眠，冷启动首请求略慢（可接受；pooled 端点缓解）。
- serverless 短连接开销：低频 alpha 无碍，放量后再引入连接池（PgBouncer/Neon pooler）。
- 匿名 cookie 丢失＝身份丢失，与存储无关，待 Phase-3 账号体系解决。
