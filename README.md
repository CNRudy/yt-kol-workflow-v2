# yt-kol-workflow

YouTube 网红（KOL）开发工作流：从关键词搜索 → 频道筛选 → 详情抓取 → 评分 → 飞书多维表格（Base）同步，再到后续红人跟进的一站式脚本集。

## 功能链路

```
关键词搜索(Phase A) → 视频筛选(Phase B) → 频道去重/初筛(Phase C)
  → 详情+最近视频抓取(Phase D) → 匹配评分 → 飞书 Base 同步 / Excel 导出
  → 派生「红人表」增量同步（保护人工列）
```

## 快速开始（通用安装）

```bash
cd assets/yt-kol-workflow
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt          # 运行依赖
pip install -r requirements-dev.txt      # 测试依赖（可选）

cp .env.example .env   # 填写 YOUTUBE_API_KEY 等
python main.py --help
```

### 环境变量（`config.py` 读取）

| 变量 | 说明 | 必填 |
|---|---|---|
| `YOUTUBE_API_KEY` | YouTube Data API v3 密钥 | 是 |
| `FEISHU_APP_TOKEN` | 目标飞书 Base 的 app_token（**统一用这个，不是 `BASE_TOKEN`**） | 自动建库时可省 |
| `FEISHU_APP_ID` / `FEISHU_APP_SECRET` | 飞书应用凭据（`FEISHU_AUTH_MODE=app` 时） | 否 |
| `FEISHU_AUTH_MODE` | `auto` / `cli` / `app`，默认 `auto` | 否 |
| `FEISHU_CLI_PROFILE` | lark-cli 用户登录 profile，默认 `kol-workflow` | 否 |
| `FEISHU_LARK_CLI_PATH` | lark-cli 可执行文件绝对路径 | **新机器必填** |
| `FEISHU_HTTPS_PROXY` | 访问飞书/YouTube 的 HTTPS 代理 | 视网络 |
| `FEISHU_HONGREN_TABLE_ID` | 派生「红人表」ID（不填用默认，仅适配既有主库） | 否 |

> 完整清单见 `.env.example`。所有表 ID / lark-cli 路径 / 代理均可用环境变量覆盖，不再需要改代码。

## 配置你自己的 Base

本仓库**不绑定任何飞书 Base**。运行前请在 `.env` 填写以下必填项：

```bash
FEISHU_APP_TOKEN=<你的 Base app_token，Base URL 里 base/ 后的那串>
FEISHU_TABLE_DETAIL=<网红详情表 table_id，以 tbl 开头>
FEISHU_TABLE_VIDEO=<视频数据表 table_id>
FEISHU_TABLE_INFLUENCER_VIDEO=<网红视频表 table_id>
FEISHU_TABLE_TASK=<搜索任务表 table_id>
FEISHU_HONGREN_TABLE_ID=<派生红人表 table_id>   # 可选；首次启用 sync_hongren_table.py 时再填
```

> 历史提示：本仓库作者曾在 `write_user_base.py` / `sync_hongren_table.py` 中以默认值
> 的形式硬编码其个人主库 ID；2026-09-08 已彻底清空默认值，要求所有 Base / 表 ID
> **必须**通过环境变量显式传入。**不要把任何 Base token 提交到仓库**。

## 正式脚本 vs 历史脚本

- **正式入口**：`main.py`（批量/单关键词全链路）、`refresh_promo_lark.py`（补算推广/评分）、`sync_hongren_table.py`（派生红人表增量同步）、`export_local_cache.py`（导出本地缓存）。
- **历史/一次性脚本**：位于 `_archive/`（按项目归档）与 `母婴类/<产品>/scripts/`（类目化产品脚本），**只 `mv` 不删除**，不代表当前受支持路径。

## 字段规范（2026-09-07 起）

飞书「网红详情表 / 红人表」已删 `匹配关键词`、`匹配排序组`（保留 `来源关键词`），新增 `领域` 单选（12 选项，由「频道初步判断」文本拆出）。写回飞书时不要引用已删除字段。报价/佣金/合作进度在金山文档，不在飞书。

## 测试

```bash
python -m pytest tests/ -q
```

## 目录

```
config.py          配置（.env / CLI / 品牌排除）
main.py            主入口（批量 + 单关键词）
workflow/          Phase A/B/C/D + 状态续传
youtube/           YouTube API 客户端 + 配额追踪
filter/            筛选/评分/邮箱/推广识别/分类
feishu/            飞书 REST/CLI 传输层 + 字段映射
export/            Excel 导出
docs/              表映射、架构等防错文档
tools/             诊断脚本
_archive/          历史归档（不删除）
```
