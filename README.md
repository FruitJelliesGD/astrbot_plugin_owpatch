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
| `/owpatch query <月份>` | 查询指定月份的补丁日期列表 |
| `/owpatch query <月份> <日期>` | 查看指定日期补丁的完整内容 |
| `/owpatch cache` | 批量预热历史补丁到本地永久缓存 |
| `/owpatch cache status` | 查看本地缓存统计 |
| `/owpatch help` | 显示帮助信息 |

**示例：**

```
/owpatch bind                    # 在当前群聊/私聊绑定
/owpatch status                  # 查看状态
/owpatch check                   # 立即检查（有记录时回复最新补丁日期）
/owpatch query 4                 # 查询4月所有补丁日期
/owpatch query 5 12              # 查看5月12日补丁详情
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
