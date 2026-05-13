# Changelog

## v1.2.1 (2026-05-26)

### ✨ 新功能
- **`/owpatch query` 无参数模式**：不再提示错误，改为自动获取当前最新补丁并直接推送（月初自动回溯上月）
  - 联网获取优先，失败后回退本地永久缓存
  - 包含 Delta 差异对比和合并转发推送
- **`/owpatch query YYYY` 年份查询功能修复**：恢复按年份查询全年补丁汇总的能力
  - 全年模式遍历所有月份：优先读缓存，缓存缺失时联网获取
  - 自动处理边界：2016 年仅查询 5~12 月，当年仅到当前月
  - 去重后按日期降序展示，附典型查询提示
- **新增 `YYYY MM [DD]` 空格分隔格式**：现在支持空格分隔的年月日参数，不再支持斜杠格式
  - `/owpatch query 2016 5` → 列出 2016 年 5 月补丁
  - `/owpatch query 2016 5 4` → 推送 2016 年 5 月 4 日补丁
  - （斜杠格式如 `2016/5` 不再支持）

### 🔧 优化
- **参数解析重写**：`cmd_query` 新增第三个参数 `arg3` 捕获所有空格 token，内联解析逻辑，移除 `_parse_query_month` 方法
- **智能格式识别**：自动区分 `月 日`、`年 月`、`年 月 日` 三种空格分隔输入

### 📦 文件变更
| 文件 | 变更 |
|---|---|
| `main.py` | `cmd_query` 签名改为 3 参数 + 内联空格解析；移除 `_parse_query_month`；所有指令/指令组新增中文别名 |
| `message_builder.py` | `build_help_message` 中英双语指令 + 空格分隔提示 |
| `CHANGELOG.md` | 本版记录 |

### 🌏 汉化
- **指令组中文别名**：`owpatch` ↔ `ow补丁`
- **所有子指令中文别名**：`绑定` / `解绑` / `状态` / `帮助` / `缓存` / `检查` / `查询` / `翻译`
- 例：`/ow补丁 查询 2016 5 4`、`/ow补丁 绑定`、`/ow补丁 翻译`
- 英文原指令继续可用，双语并行

## v1.2.0 (2026-05-13)

### ✨ 新功能
- **新增 `/owpatch translate` 指令**：查询补丁日志后发送该指令，调用 AstrBot 当前大模型将补丁内容逐章节翻译为中文
  - 保留原 H4/H5 分段结构和合并转发方式
  - 读取插件内置 `skills/overwatch-patch-translation/` 的翻译规范（SKILL.md）和 346 条术语对照表（overwatch_terms.json）构建 system prompt
  - 逐章节调用 LLM 翻译，每章完成后显示进度
  - 错误处理：单章翻译失败时保留英文原文，不中断整体流程
  - 降级策略：skill 文件缺失时使用内置 ~20 个核心术语兜底
- **翻译缓存**：翻译结果按日期+内容哈希持久化到磁盘，同一补丁内容未变更时秒级复用
  - 缓存文件：`data/plugin_data/astrbot_plugin_owpatch/cache/translation/{date}_{hash_prefix}.json`
  - 联网检测到补丁更新后自动触发重新翻译（哈希不匹配）

### 🔧 优化
- **SKILL.md 重构**：从 400+ 行 Agent 工作流文档精简为 ~120 行 LLM system prompt 模板
  - 新增「角色」和「输入说明」章节，声明逐章翻译场景
  - 保留 5 类核心翻译规则（术语处理、创意翻译、格式规范、风格统一、其他）
  - 删除工作流步骤、文件管理、维护指南等不适用于 system prompt 的内容
  - 新增「输出要求」章节，明确仅返回翻译文本、不含核对报告
- **`_conf_schema.json`**：新增 `translate_prompt` 可选配置项（type: text）

### 📦 文件变更
| 文件 | 变更 |
|---|---|
| `translator.py` | **新建** — 翻译核心模块 |
| `CHANGELOG.md` | **新建** — 本文件 |
| `main.py` | 新增 `_last_query` 缓存、`cmd_translate` 指令、`cmd_query` 写入缓存 |
| `config.py` | 新增 `KEY_TRANSLATE_PROMPT`、`DEFAULT_TRANSLATE_PROMPT` |
| `_conf_schema.json` | 新增 `translate_prompt` 配置项 |
| `message_builder.py` | `build_help_message()` 新增 `/owpatch translate` |
| `skills/overwatch-patch-translation/SKILL.md` | 重构为 system prompt 模板 |
| `skills/overwatch-patch-translation/scripts/overwatch_terms.json` | **添加** — 346 条术语对照表 |
| `metadata.yaml` | 版本号 v1.0.0 → v1.2.0 |

---

## v1.0.0 (2026-05-12)

### ✨ 初始版本
- 守望先锋补丁日志自动监控与推送
- 支持手动检查、历史补丁查询、补丁变更对比（Delta）
- 合并转发消息推送（OneBot 嵌套转发 + AstrBot Chain 回退）
- 本地永久缓存、定时调度、Stadium 内容过滤
