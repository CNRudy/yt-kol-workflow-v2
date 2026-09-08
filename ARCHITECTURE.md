# 项目架构总览

> 与 `docs/TABLE_MAP.md`（飞书表/本地缓存映射）配合阅读。
> **改任何脚本前先看这两份文档**，避免重复的脚本副本、错误的路径、认错表。

最后更新：2026-09-07

---

## 一、目录结构

```
yt-kol-workflow/
├── ARCHITECTURE.md        ← 本文件：目录与脚本索引
├── docs/
│   └── TABLE_MAP.md       ← 飞书表 / 本地缓存映射（唯一真源，必读）
│
├── main.py                CLI 主入口（batch 爬取 / 打分 / 导出）
├── config.py              配置管理
│
│  ── 飞书读写 ──
├── write_user_base.py     写主库 4 张表（先清空再写）
├── sync_hongren_table.py  ⭐派生「红人表」增量同步（保护人工列，常用）
├── build_hongren_table.py   建红人表（删旧重建，仅首次用）
├── sync_vyh0_upsert.py      本地 batch 增量 upsert 进主库（不清空）
│
│  ── 评分 / 补算 ──
├── score_influencers.py   离线补算 品牌匹配度 / 优先级 / 推荐理由
├── refresh_promo_lark.py  离线补算 亚马逊推广经验 + 联系邮箱
├── rescore_local_batch.py 用当前画像重算本地 batch 输出
├── rescore_soccer.py      足球护膝线补算
│
│  ── 本地缓存 ──
├── export_local_cache.py  飞书 5 表 → local_cache/ JSON
├── local_cache_reader.py  秒级读取 local_cache/
│
│  ── 输入 / 输出 ──
├── keywords/              关键词文件（按产品线）
├── output/                批量产出（160MB，按批次目录）
├── local_cache/           本地表缓存（见 TABLE_MAP 第四节）
├── seen_channels.json     已抓取频道去重表
│
│  ── 核心模块 ──
├── youtube/   YouTube API 封装
├── filter/    过滤与分级（含 promo_detector / email_extractor）
├── workflow/  工作流编排
├── feishu/    飞书封装
├── utils/     工具函数
├── tests/     测试
│
│  ── 类目（产品线）──
├── 母婴类/WEMOH推车 MCB/        ← 推车 wagon 专线
│   ├── scripts/   爬取 / 整合 / 发布 / 评分 / 备份 / 合并
│   ├── data/      stroller_fetched_videos_20260904.json（视频缓存）
│   └── output/    产出 + backup/（5 表本地备份）
│
├── tools/     诊断统计（count_tables / diff_hongren）
└── _archive/  🗄 归档（一次性脚本 + 临时文件，未删除，可还原）
```

---

## 二、类目目录约定（2026-09-07 确立）

**三层：类目 → 产品 → 表**

```
云盘/母婴类/                       ← 类目文件夹（网页建）
   └── WEMOH推车 MCB/               ← Base，用产品名
         ├── 网红详情表              ← 表名不带前缀
         └── ...

yt-kol-workflow/母婴类/             ← 本地同类目目录
   └── WEMOH推车 MCB/
         ├── scripts/ data/ output/
```

**规则**：
1. 产品专属脚本放 `类目/产品/scripts/`，**不要放根目录**（历史上根目录和类目目录各存一份，内容还不同）。
2. 脚本内路径用 `os.path.dirname(__file__)` 推导，**不引用根目录**，避免改一处漏一处。
3. 新建产品线时复制 `母婴类/WEMOH推车 MCB/` 的结构。

---

## 三、常用操作速查

| 要做什么 | 用哪个脚本 |
|---|---|
| 派生红人表跟进（主表改完必跑） | `./.venv/bin/python sync_hongren_table.py --dry-run` → 去 `--dry-run` |
| 飞书 → 本地缓存 | `./.venv/bin/python export_local_cache.py` |
| 读本地缓存（秒级，不调 API） | `local_cache_reader.py` |
| 新建产品类目库 | 复制 `母婴类/WEMOH推车 MCB/scripts/publish_wagon_5table.py` |
| 产品库 → 本地备份 | `母婴类/*/scripts/backup_mcb_local.py` |
| 产品库 → 并入主库 | `母婴类/*/scripts/merge_mcb_to_main.py --dry-run` → `--apply` |
| 统计各表行数 | `./.venv/bin/python tools/count_tables.py` |
| 对比主表 vs 红人表差异 | `./.venv/bin/python tools/diff_hongren.py` |

跑任何脚本前务必设代理（YouTube 抓取需要）：
```bash
export HTTPS_PROXY=http://127.0.0.1:7890   # Clash 端口动态，先确认
```

---

## 四、`_archive/` 归档内容（未删除，需要可还原）

| 子目录 | 内容 |
|---|---|
| `20260814_batches/` | 113 个 `_batch_*.json` 等批量写入中间文件（7.6MB） |
| `20260817_restore/` | `restore_cached_fields.py` / `fix_missing_records.py` |
| `20260903_vyh0_fix/` | `vyh0_*` / `check_nosource*` / `restore_nosource` / `clean_main_dupes` |
| `cfc1_202608/` | CFC1 项目 5 个脚本 |
| `us_baby_202608/` | WEMOH 美国婴儿摄像头项目 3 个脚本 |
| `probe_debug/` | `probe*` / `feishu_debug` / `feishu_inspect` / `analyze_xlsx` |
| `legacy_202607_feishu/` | ⚠️ `feishu_sync_v2.py` + `sync_to_user_base.py`（详见下方警告） |
| `legacy_stroller_20260904/` | 推车脚本**根目录旧副本** + output 重复件（正式版在 `母婴类/WEMOH推车 MCB/`） |

> 归档原则：**只 `mv` 不 `rm`**。归档脚本里的常量（如已删除的 Base token）仅供参考，不再维护。

### ⚠️ 归档脚本风险提示

- **`feishu_sync_v2.py`**（7 月早期，App/tenant 模式直连 OpenAPI，不走 lark-cli）
  - 指向早期测试库 `JB3Vb82DuaaGKlswusmc71I8nKc`（网红详情表 145 行 / 视频数据表 187 行 / 网红视频表 1450 行），**不是主库**。
  - 不带参数时"表已有记录则跳过写入"，相对安全；但 **`--reset` 会 `batch_delete` 清空该库的表** → **勿跑**。
  - 硬编码 `APP_ID` / `APP_SECRET` 明文（废弃 app，仅作卫生提醒）。
- **`sync_to_user_base.py`**（一次性建库脚本）
  - 第 7 行 lark-cli 路径为**已失效旧路径** `node/versions/22.22.2/bin/`（该文件已不存在）→ 现在直接跑会 `FileNotFoundError`，**已死**。
  - 即便修好路径，也只是新建一个名为「KOL网红开发工作流(可编辑版)」的垃圾 Base，不删不覆盖现有数据。

两者均**无任何脚本引用**（已全库 grep 确认），归档不影响任何现有流程。

---

## 五、2026-09-07 整理时修掉的问题

1. **推车脚本双份**：根目录 + 类目目录各一份，其中 `score_stroller.py` / `publish_stroller_base.py` 内容不同 → 根目录版归档，正式版统一在类目目录。
2. **路径指向根目录**：`publish_wagon_5table.py` 的 `VIDEO_CACHE` / `KEYWORDS_FILE` 指向根目录，实际文件在类目目录 → 改用 `CATALOG` 常量推导。
3. **依赖已删源库**：`publish_wagon_5table.py` 从 HFBkb（已删）读 schema → 改为从 `Yz9GbeS` 自举，脚本恢复可重跑。
4. **lark-cli 路径失效**：`write_user_base.py` 仍用旧路径 `node/versions/22.22.2/bin/` → 改为 `cli-connector-packages/bin/`。
5. **根目录 193 项**：111 个 `_batch_*.json` + 27 个一次性脚本散落 → 归档，根目录剩 32 文件 + 12 目录。
