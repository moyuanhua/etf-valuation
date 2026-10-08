# ETF 长期价值仪表盘

面向**长期持有者**的 ETF 估值看板：每个交易日自动更新 20 个核心指数的估值数据，用「便宜（估值）· 能赚（质量）· 跌透（回撤）」三维打分，并内置可对话的 AI 助手。

在线地址：https://moyuanhua.github.io/etf-valuation/

## 功能

- 📊 **每日自动更新**：工作日北京时间约 18:30 由 GitHub Actions 抓数并提交，GitHub Pages 即时可见。
- 🏷️ **长期价值评分（0-100）**：见下方评分方法，比「只看便宜」更贴近长期持有的决策逻辑。
- 🤖 **AI 对话**：页面右下角可开启 AI 助手，基于当日快照回答「哪些指数低估」「对比两个指数」等问题。支持任何 OpenAI 兼容接口（DeepSeek / 火山方舟 / 硅基流动等），**API Key 仅存本机浏览器 localStorage**。
- 🌍 **跨市场**：覆盖 A 股、港股、中概、美股、德国指数，并给出每只指数的可交易 ETF 对照。

## 评分方法（长期价值分）

| 维度 | 权重 | 口径 |
|---|---:|---|
| 估值 | 45% | 综合估值分位 = PE 分位 ×60% + PB 分位 ×40%，分位越低得分越高 |
| 质量 | 35% | ROE ×70% + 股息率 ×30%（ROE 15%、股息 5% 封顶） |
| 回撤 | 20% | 当前价距滚动高点的跌幅，跌越深得分越高（完整覆盖为近十年窗口） |

评级：**≥75 深度价值 · ≥60 价值区 · ≥45 合理 · ≥30 偏贵 · <30 高估**

> 持仓主题指数（中证机器人/电池主题/机床/电网设备主题/人工智能主题/港股通互联网/上证综合，
> 在页面上以 ⭐ 标记）：蛋卷未覆盖，暂无十年历史分位与 PB/ROE，显示为「观察」不参与评分，
> 仅展示中证官网 PE/股息快照与近 2 年回撤。ETF→指数映射经天天基金与国信接口双重确认。

> 质量维度用于过滤「便宜但垃圾」的价值陷阱；回撤权重被压到 20%，避免把「跌得多」误当「有价值」。

## 数据来源

| 数据 | 来源 |
|---|---|
| PE / PB 及其历史百分位、股息率、ROE | 蛋卷基金指数估值接口（`danjuanapp.com/djapi/index_eva/dj`） |
| A 股指数行情（算回撤） | AKShare（中证系指数） |
| 港股行情 | 恒指官网 Factsheet + Yahoo Finance |
| 美股/海外行情 | Yahoo Finance |

数据仅作研究参考，不构成投资建议。

## 部署

仓库已配置 GitHub Actions 自动更新。首次部署只需一步：

1. GitHub 仓库 **Settings → Pages → Source** 选择 **`Deploy from a branch`** → 分支 **`main`** → 目录 **`/docs`** → Save。
2. 片刻后访问 `https://<用户名>.github.io/etf-valuation/` 即可（首次构建约需 1-2 分钟）。

手动更新：Actions → **Update ETF dashboard data** → Run workflow；本地运行见下方「本地开发」。

> 持仓主题指数行情走国信接口，需要给仓库配置 secret：**Settings → Secrets and variables → Actions → New repository secret**，
> Name 填 `GS_API_KEY`，Value 填国信 Skill 的 API Key。若未配置，该步骤自动跳过（保留旧数据，不会报错）。

**密钥管理原则（本项目）**：所有 Key 只从环境变量 / GitHub Secret 注入，不写进任何代码与文档；
仓库禁入 Key（`.gitignore` 已忽略本地密钥文件，历史提交中亦无 Key）。

## 本地开发

```bash
python3.12 -m venv .venv
.venv/bin/pip install -r requirements.txt
export GS_API_KEY="你的国信Key"   # 仅主题指数行情需要
.venv/bin/python scripts/fetch_djeva.py          # 估值（蛋卷）
.venv/bin/python scripts/fetch_cn_csindex.py     # A股行情
.venv/bin/python scripts/fetch_hk_hsi.py         # 港股行情
.venv/bin/python scripts/fetch_us_yf.py          # 美股/海外行情
.venv/bin/python scripts/fetch_theme_csindex.py  # 持仓主题指数（需 GS_API_KEY）
.venv/bin/python scripts/compute_metrics.py
.venv/bin/python scripts/build_assets.py
.venv/bin/python -m http.server 8017 --directory docs   # http://127.0.0.1:8017/
```

评分与 AI 对话逻辑均在 `docs/index.html` 前端内（纯客户端计算），无需后端。

## 致谢

本项目基于 [bryanzhang1024/etf-dashboard-auto](https://github.com/bryanzhang1024/etf-dashboard-auto) 修改：
- 重写评分：由「Value/Pain 二分」改为「估值 45% + 质量 35% + 回撤 20%」的长期价值分。
- 新增 AI 对话面板（客户端直连 OpenAI 兼容接口）。

## 免责声明

本项目及 AI 输出仅供个人研究参考，不构成任何投资建议。数据来自第三方公开接口，可能存在延迟或错误。
