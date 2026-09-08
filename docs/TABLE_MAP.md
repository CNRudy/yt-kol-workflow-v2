# 飞书表映射 · 唯一真源（Single Source of Truth）

> ⚠️ **任何脚本/会话在引用飞书表或本地缓存前，必须先读本文件。**
> 历史事故：`videos.json` 被误当成「网红视频表」（实际是**视频数据表**），
> 「网红视频表」的本地缓存是 `channel_videos.json`。两者名字容易反，以本表为准。

最后更新：2026-09-07

---

## 一、Base 清单

| Base 名称 | base_token | 用途 | 状态 |
|---|---|---|---|
| **主库（母池）** | `VyH0bJ1WBaCKIWsn9gVcBVc4nmc` | 全产品线红人总表 | ✅ 在用 |
| **WEMOH推车 MCB** | `Yz9GbeSbfabon9sS1DKcNY25ngm` | 母婴类 / 推车 wagon 专线 | ✅ 在用 |
| CFC1（AirTag 护照包） | `FIhJbkQjda3mmqsSq8Jc1pYNnch` | CFC1 项目 | 🗄 归档项目 |
| ~~WEMOH推车Wagon网红库~~ | ~~`HFBkbxyQNa4pjYsrXOJcRwU5nHb`~~ | 推车旧源库 | ❌ **已删除**（2026-09-07），勿再引用 |
| ~~WEMOH美国婴儿车载摄像头红人库~~ | 运行时创建 | — | 🗄 归档项目 |
| 早期测试库（7月） | `JB3Vb82DuaaGKlswusmc71I8nKc` | 网红详情表 145 / 视频数据表 187 / 网红视频表 1450 | ⚠️ 遗留，**不是主库**。`feishu_sync_v2.py --reset` 会清空它 → 勿跑 |

主库链接：https://pcn8zy4grswl.feishu.cn/base/VyH0bJ1WBaCKIWsn9gVcBVc4nmc
推车库链接：https://pcn8zy4grswl.feishu.cn/base/Yz9GbeSbfabon9sS1DKcNY25ngm

---

## 二、主库 VyH0 表结构

| 飞书表名 | table_id | 行数（2026-09-07） | 说明 |
|---|---|---|---|
| 网红详情表 | `tbl4rnFUM9jJXvCQ` | 1874 | **主表**，所有红人明细 |
| 红人表 | `tblPKFENcpk8xnZH` | 1874 | **派生表**（由详情表同步，含人工跟进列） |
| 视频数据表 | `tblDQi8dEGhkjZyy` | 6698 | 按关键词搜到的视频 |
| 网红视频表 | `tblcHBORC90WWn05` | 22129（**已超限**） | ⚠️ 飞书端停同步，数据放本地 |
| 搜索任务表 | `tblLT1yf9ioEhJOh` | 52 | 关键词爬取任务记录 |
| 足球红人匹配评分 | `tble06acWNOPB6vX` | — | 足球线专项评分 |

> **网红视频表超限说明**：飞书报 `800040832` 行数上限，2026-09-03 决定「不再同步到飞书、数据留本地」。
>
> **字段治理（2026-09-07）**：主表与 MCB 详情表均删「匹配关键词」（与「来源关键词」重复，保留后者），新增「领域」单选（从「频道初步判断」文本拆出，12 选项）。主表回填 1425 行、MCB 回填 112 行（「待确认」留空）。被删字段值备份于 `output/backup/20260907_字段治理备份/`（主表 640 行 / MCB 22 行）。

---

## 三、WEMOH推车 MCB（Yz9GbeS）表结构

| 飞书表名 | table_id | 字段数 | 行数 |
|---|---|---|---|
| 网红详情表 | `tblzPpg6Uj197wND` | 36 | 201 |
| 红人表 | `tblIDj93S485Yn00` | 24 | 201 |
| 视频数据表 | `tblD2u6OAVkyQLdF` | 19 | 766 |
| 网红视频表 | `tblvBIWnJeK2FoS8` | 14 | 766 |
| 搜索任务表 | `tblN15BrWVfGTeJy` | 11 | 10 |

> 2026-09-07 已按「主表为主」补齐：详情表补 9 个主表有而 MCB 缺的字段，并从主表回填 148 行（只补空不覆盖）。
> 同日删除「分类」「KOL Name」两个字段（产品名本身即代表类目；KOL Name 89% 是占位符「手动确认」）。
> 同步脚本：`母婴类/WEMOH推车 MCB/scripts/sync_main_to_mcb.py`（默认 dry-run，`--apply` 写入）。
> ⚠️ **备注字段只存在于红人表**（详情表两边都没有），别往详情表写备注，会报 `not_found`。

命名约定：云盘 `母婴类/` 文件夹 → Base 用**产品名**（`WEMOH推车 MCB`）→ **表名不带前缀**。

---

## 四、本地缓存映射（⚠️ 最容易搞混的一段）

路径：`local_cache/`，结构统一为 `{"meta": {...}, "records": [...]}`

| 本地文件 | 对应飞书表 | table_id | 记录数 |
|---|---|---|---|
| `influencers.json` | **网红详情表** | `tbl4rnFUM9jJXvCQ` | 1821（09-04 导出） |
| `hongren.json` | **红人表** | `tblPKFENcpk8xnZH` | — |
| `videos.json` | **视频数据表** ⚠️ | `tblDQi8dEGhkjZyy` | 5258（08-17 导出） |
| `channel_videos.json` | **网红视频表** ⚠️ | `tblcHBORC90WWn05` | 6750（09-07 更新） |
| `search_tasks.json` | **搜索任务表** | `tblLT1yf9ioEhJOh` | — |
| `_index.json` | 索引本身 | — | — |

**记忆口诀**：
- `videos.json` = **视频数据**表（先有的，名字朴素）
- `channel_videos.json` = **网红视频**表（带 `channel_` 前缀 = 挂在网红维度下）

> 判错时的兜底办法：读文件的 `meta.table_id`，与本表比对，不要靠文件名猜。

---

## 五、写入飞书的硬性坑（每次写前对照）

1. `record-batch-create/update` 必须用 `tbl` 开头真实 table_id，**表名不行**。
2. `field-update` 必须传 `--field-id`（不是 `--name`），且 `--json` **只能内联字符串**，
   用 `@file` 会返回 `ok` 但**静默不生效**。
3. `record-list` 的 `--limit` 上限 **200**，需自己分页。
4. select 字段读出可能是 **list**（如 `['持续更新']`），写入前取首元素（`_scalar()`）。
5. datetime 字段需 `YYYY-MM-DD HH:MM:SS`，10 位日期要补 ` 00:00:00`。
6. `record-batch-update` **任一字段非法整批失败**，务必先 `field-list` 取全 select 选项。
7. select 值不在选项内 → 先 `field-update` 补选项，再写记录。
8. lark-cli 真实路径：`/Users/coscod/.workbuddy/binaries/node/cli-connector-packages/bin/lark-cli`
9. YouTube API / 部分请求需走 Clash 代理 `HTTPS_PROXY=http://127.0.0.1:7890`（端口动态）。
10. 错误 JSON 走 **stderr**，需同时检查 stdout + stderr；`LARK_CLI_NO_PROXY_WARN=1` 抑制代理警告。
11. **lark-cli 没有删除 Base 的命令**，删库 / 重命名 Base / 移动文件夹只能网页手动。
12. `+table-update --name` **可以**重命名表（已验证）。
