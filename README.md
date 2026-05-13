# 守望先锋补丁监控插件 (astrbot_plugin_owpatch)

[![AstrBot](https://img.shields.io/badge/AstrBot-Plugin-blue)](https://github.com/AstrBotDevs/AstrBot)
[![Python](https://img.shields.io/badge/Python-3.10%2B-green)](https://www.python.org/)

自动监控《守望先锋》官方补丁日志，在每天凌晨窗口内轮询检查，发现新补丁后推送到绑定的会话。

## 功能

- 🕐 **定时轮询**：每天北京时间 1:50~4:00 窗口内自动检查，发现新补丁当即推送并停止当日轮询。当月补丁始终实时获取，不缓存
- 🌍 **月初双月检查**：每月 1 号同时检查上月 + 当月页面，防止美区时差遗漏补丁
- 📋 **手动检查**：`/owpatch check` 即时检查，不受窗口时间限制；无新补丁时直接报告最新补丁日期
- 📜 **历史查询**：`/owpatch query <月份>` 列出指定月份的补丁日期，支持按日期查看详情
- 🗄️ **三级本地缓存**：永久/日级/短时三级缓存，`/owpatch cache` 批量预热历史补丁到本地（毫秒级查询）
- 👤 **英雄独立分卡**：基于 `PatchNotesHeroUpdate` div 自动识别英雄改动，每个英雄独立一张合并转发卡片
- 🔍 **增量检测**：同一补丁后续追加内容时，自动识别只推送增量部分并标注"官方后加内容"
- 📨 **嵌套合并转发**：Tank/Damage/Support 等长章节自动嵌套为二级合并转发，每个英雄一条子卡片
- ⚙️ **Stadium 过滤**：可选关闭 Stadium（角斗领域）相关内容，基于 H4 标题重复检测自动过滤
- 🛡️ **首次安装基线**：首次安装时静默记录当前补丁为基线，不推送历史旧补丁
- 🔒 **并发安全**：定时任务与手动检查互斥，防止重复推送

## 安装

1. 将本仓库克隆到 AstrBot 的 `data/plugins/` 目录：

```bash
cd AstrBot/data/plugins
git clone https://github.com/FruitJelliesGD/astrbot_plugin_owpatch.git
```

2. 安装依赖：

```bash
pip install -r requirements.txt
# 或通过 AstrBot WebUI 的插件管理页面安装依赖
```

3. 在 AstrBot WebUI 中启用插件，或在插件管理页面重载插件。

## 配置

所有配置项可通过 AstrBot WebUI 的「插件配置」面板可视化编辑：

| 配置项 | 默认值 | 说明 |
|--------|--------|------|
| `check_interval_minutes` | `10` | 定时窗口内的检查间隔（分钟） |
| `window_start_time` | `01:50` | 每日检查窗口开始时间（北京时间） |
| `window_end_time` | `04:00` | 每日检查窗口结束时间（北京时间） |
| `base_url_template` | `https://overwatch.blizzard.com/en-us/news/patch-body/live/{year}/{month:02d}/` | 补丁页面 URL 模板 |
| `user_agent` | Chrome UA | HTTP 请求 User-Agent |
| `request_timeout` | `60` | HTTP 请求超时（秒），国内建议 60+ |
| `proxy` | 空 | HTTP 代理地址，如 `http://127.0.0.1:7890` |
| `cache_ttl_minutes` | `10` | 页面缓存有效期（分钟） |
| `include_stadium` | `false` | 是否包含 Stadium 角斗领域内容 |

## 指令

| 指令 | 说明 |
|------|------|
| `/owpatch bind` | 绑定当前会话接收补丁推送 |
| `/owpatch unbind` | 解绑当前会话 |
| `/owpatch status` | 查看绑定数量、最新推送、当日状态 |
| `/owpatch check` | 立即检查新补丁 |
| `/owpatch query <月份>` | 查询当年指定月份的补丁日期列表 |
| `/owpatch query <月份> <日期>` | 查看当年指定日期补丁的完整内容 |
| `/owpatch query <YYYY/MM>` | 查询指定年月的补丁（跨年查询，如 `2026/4`） |
| `/owpatch cache` | 批量预热历史补丁到本地永久缓存 |
| `/owpatch cache status` | 查看本地缓存统计 |
| `/owpatch help` | 显示帮助信息 |

**示例：**

```
/owpatch bind                    # 在当前群聊/私聊绑定
/owpatch status                  # 查看状态
/owpatch check                   # 立即检查（有记录时回复最新补丁日期）
/owpatch query 4                 # 查询当年4月所有补丁日期
/owpatch query 5 12              # 查看当年5月12日补丁详情
/owpatch query 2026/4            # 查询2026年4月补丁（跨年查询）
/owpatch cache                   # 批量预热所有历史补丁
/owpatch cache status            # 查看缓存统计
```

## 消息格式

- **单卡短章节**：无 H5 子节且 <500 字的章节作为单张卡片
- **展开长章节**：含 ≥2 个 H5 子节（英雄改动）或 >500 字的章节展开为嵌套合并转发
- **增量推送**：补丁追加内容时单独推送，标注 `📌 官方为 XX-XX 补丁补充了以下内容（后加）：`
- **全平台兼容**：OneBot v11 支持原始嵌套协议；其他平台降级为 AstrBot 组件平铺

## 工作原理

```
每天 1:50~4:00 → 每10分钟轮询 → 获取当月补丁页面（始终实时）
    ↓
月初(1号)同时检查上月+当月 URL（防美区时差）
    ↓
解析页面 HTML → 正则切 H4 片段 → 提取 PatchNotesHeroUpdate 英雄卡片
    ↓
与本地记录比较：
  ├─ 日期变化 → 全新补丁 → 推送全量 + 节级哈希
  ├─ 整版哈希变化 + 日期相同 → 增量检测 → 只推送变化章节
  └─ 无变化 → 跳过
    ↓
构建消息链 → 遍历所有绑定 UMO → 逐一推送
    ↓
更新状态文件 → 当日不再推送
```

## 缓存策略

插件采用三级本地缓存，平衡实时性与查询性能：

| 缓存级别 | 适用范围 | TTL | 说明 |
|---------|---------|-----|------|
| 短时缓存 | 当月页面 | `cache_ttl_minutes`（默认 10 分钟） | 仅在定时窗口内生效，避免短时间重复请求暴雪服务器 |
| 日级缓存 | 上月页面 | 1 天 | 上月补丁已稳定，每天刷新一次即可 |
| 永久缓存 | 更早的历史月份 | 无过期 | 通过 `/owpatch cache` 批量预热，查询秒级响应；每月初自动将上月升级为永久缓存 |

> **注意**：当月补丁页面在定时窗口外（如手动 `/owpatch check`）始终实时获取，不走缓存。

## 首次安装行为

首次安装并启用插件后，插件会**静默建立基线**：

1. 自动获取当前最新补丁的日期和内容哈希，写入本地状态文件
2. **不会推送任何历史补丁**，避免刷屏
3. 之后仅当暴雪发布**新补丁**（日期变化）或对当前补丁**追加内容**（哈希变化）时，才会向绑定的会话推送

> 如需查看历史补丁，请使用 `/owpatch query` 指令手动查询。

## 数据存储

所有插件运行时数据存储在 AstrBot 的数据目录下，而非插件目录本身：

```
data/plugin_data/astrbot_plugin_owpatch/
├── state.json          # 状态文件（最新补丁日期/哈希、绑定会话、当日推送标记）
└── cache/              # 本地缓存目录（历史补丁页面缓存）
```

- 如需**重置插件状态**（清空绑定、重新建立基线等），删除 `state.json` 后重载插件即可
- 如需**清理缓存**，删除 `cache/` 目录后重载插件，或使用 `/owpatch cache` 重新预热

## 常见问题

### Q: 插件启动后没有收到任何推送？

这是正常行为。首次安装会建立基线，不会推送历史补丁。等待暴雪发布下一个补丁后即可收到推送。你也可以使用 `/owpatch check` 手动检查当前状态。

### Q: 请求超时或无法获取补丁页面？

- 检查网络是否能正常访问 `overwatch.blizzard.com`
- 适当调大 `request_timeout`（国内网络建议 60~120 秒）
- 配置 HTTP 代理：在插件配置中将 `proxy` 设为 `http://127.0.0.1:7890`（根据你的代理软件调整端口）

### Q: 如何关闭 Stadium（角斗领域）内容？

`include_stadium` 默认为 `false`（关闭）。当开启时，英雄信息中会包含 Stadium 模式的专属改动；关闭后基于 H4 标题重复检测自动过滤 Stadium 相关章节。

### Q: 绑定后收不到推送？

使用 `/owpatch status` 确认当前会话是否在绑定列表中。注意：群聊和私聊的绑定是独立的，需分别在对应会话中执行 `/owpatch bind`。

## 项目结构

```
astrbot_plugin_owpatch/
├── main.py               # 插件主入口（Star 子类，指令注册，核心逻辑）
├── config.py             # 配置常量与默认值
├── fetcher.py            # HTTP 页面获取（httpx + TTL 缓存 + 代理）
├── parser.py             # HTML 解析（H4 边界 + PatchNotesHeroUpdate + Stadium 过滤）
├── state_manager.py      # JSON 状态持久化（补丁追踪 + UMO + 节级哈希）
├── message_builder.py    # AstrBot 消息链构建（平铺卡片）
├── forward_builder.py    # OneBot 原始嵌套合并转发构建器
├── cache_manager.py      # 三级本地缓存（永久/日级/短时）
├── scheduler.py          # asyncio 定时调度器（北京时间窗口）
├── _conf_schema.json     # 可视化配置项定义（8 项）
├── metadata.yaml         # 插件元数据
└── requirements.txt      # Python 依赖
```

## 依赖

- `httpx >= 0.27.0` — 异步 HTTP 请求
- `beautifulsoup4 >= 4.12.0` — HTML 解析
- `lxml >= 5.0.0` — XML/HTML 解析器

## 开发

本项目遵循 [AstrBot 插件开发规范](https://astrbot.app/dev/star/plugin-new.html)。

## 许可证

MIT License
