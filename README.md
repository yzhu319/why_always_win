# 赢典AI · V1.0（alpha 内测版）

> 同一事实，多维赢解；全球视野，立于不败。

按 PRD 第 10 章（V1.0 首发必备核心模块）实现的极简落地版，用于 alpha 内部测试。

## 已实现模块（对照 PRD §10）

| PRD 模块 | 实现情况 |
|---|---|
| 10.1 自由对话搜索首页 | ✅ 开放式输入框 + 连续上下文追问（SSE 流式输出） |
| 10.1 四大选项卡 | ✅ 写文案 / 辣评锐评 / 赢面分析 / 实时时事赢面 |
| 10.1 平台字数适配 + 一键复制 | ✅ 微博/小红书/知乎/抖音规则内置于提示词；复制自动附带品牌落款 |
| 10.2 多维赢面分析 | ✅ 争议点拆解、逻辑漏洞、法理/历史/道义/舆论四维打分、最优取胜角度 |
| 10.2 权威基础引证 | ✅ 内置轻量权威库（`app/data/knowledge.json`：联合国宪章、2758号决议、开罗宣言等），强制标注来源、禁止编造 |
| 10.2 多风格文案 | ✅ 官方严谨 / 理性客观 / 犀利锐评 / 抖机灵 |
| 10.2 基础海外信源 | ✅ Reddit 公开热点接入（10 分钟缓存）+ 一键时事赢面研判；Twitter/YouTube 二期接入 |
| 10.3 双层敏感审核 | ✅ 本地敏感词库（第一层）+ 大模型语义校验（第二层，可用环境变量关闭） |
| 10.3 风险提示与改写 | ✅ 输出附合规标签：合规可发 / 轻微风险+建议 / 高危拦截 |
| 10.3 用户免责声明 | ✅ 页面底部常驻 |
| 10.4 赢币付费体系 | ✅ 新用户 10 币、每日登录 +3、每次研判扣 1；9.9/50币 与 19.9/月度会员（内测为模拟充值）；账单中心 |
| 10.5 品牌基础体验 | ✅ 跳动赢字光标（输入/生成加速跳动）、生成完成金色盖章特效、典典加载动画+状态文案轮播、水印落款 |

V1.0 明确舍弃（PRD §10.6）：表情包商城、勋章等级、浏览器插件、大V模板库、404 彩蛋等，均未实现。

## 快速启动（本地）

```bash
cd why_always_win
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

export GEMINI_API_KEY=AIzaSyxxxx   # 必填
uvicorn app.main:app --port 8000
```

浏览器打开 http://localhost:8000

## Vercel 部署

仓库已含 `vercel.json` + `api/index.py`，在 Vercel 导入本 GitHub 仓库即可：

1. vercel.com/new → Import `yzhu319/why_always_win`
2. Environment Variables 添加 `GEMINI_API_KEY`
3. Deploy（后续 push 到 `main` 自动重新部署）

注意：Vercel serverless 文件系统只读，SQLite 存 `/tmp`，**赢币数据是临时的**（冷启动即重置）。alpha 内测可接受，正式版需迁移 Vercel KV / Postgres。

## 配置项（环境变量）

| 变量 | 默认 | 说明 |
|---|---|---|
| `GEMINI_API_KEY` | — | Gemini API key，必填 |
| `YD_MODEL` | `gemini-2.5-flash` | 生成与合规校验所用模型 |
| `YD_LLM_COMPLIANCE` | `1` | 大模型二次合规校验开关（`0` 关闭，仅本地敏感词） |
| `YD_DB` | 见说明 | SQLite 路径；Vercel 上自动用 `/tmp/yd_app.db` |
| `YD_PORT` | `8000` | 服务端口（仅本地） |

## 目录结构

```
app/
  main.py          # FastAPI 入口：生成(SSE流式)、赢币、账单、热点接口
  prompts.py       # 品牌人设 + 四大选项卡提示词 + 风格/平台/强度规范
  compliance.py    # 双层合规审核
  db.py            # 赢币/会员/账单（SQLite）
  trending.py      # 海外热点抓取（Reddit 公开 JSON，10 分钟缓存）
  data/
    knowledge.json       # 内置权威引证库
    sensitive_words.txt  # 本地敏感词库（占位，运营维护）
static/
  index.html       # 单页前端（品牌 VI + 全部交互）
```

## alpha 内测注意事项

- **充值是模拟的**：点击充值直接到账并记账，未接入支付。上线前需接微信/支付宝。
- **敏感词库是占位的**：`app/data/sensitive_words.txt` 需运营补充完整词库。
- **用户体系是匿名 cookie**：换浏览器即新用户。二期接手机号/微信登录。
- **海外信源**：内测版仅接入 Reddit 公开 JSON；Twitter/YouTube 需要 API 资质，二期接入。
- 免责声明：本工具仅提供文案研判辅助，内容发布由用户自行负责。
