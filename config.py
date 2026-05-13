"""
守望先锋补丁监控插件 — 配置常量与辅助
"""

# ---------- 配置项 key（与 _conf_schema.json 对应）----------

KEY_CHECK_INTERVAL = "check_interval_minutes"
KEY_WINDOW_START = "window_start_time"
KEY_WINDOW_END = "window_end_time"
KEY_BASE_URL_TEMPLATE = "base_url_template"
KEY_USER_AGENT = "user_agent"
KEY_REQUEST_TIMEOUT = "request_timeout"
KEY_PROXY = "proxy"
KEY_CACHE_TTL = "cache_ttl_minutes"
KEY_INCLUDE_STADIUM = "include_stadium"

# ---------- 默认值 ----------

DEFAULT_CHECK_INTERVAL = 10          # 分钟
DEFAULT_WINDOW_START = "01:50"       # HH:MM 北京时间
DEFAULT_WINDOW_END = "04:00"
DEFAULT_BASE_URL = (
    "https://overwatch.blizzard.com/en-us/news/patch-body/live/{year}/{month:02d}/"
)
DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/131.0.0.0 Safari/537.36"
)
DEFAULT_REQUEST_TIMEOUT = 60         # 秒
DEFAULT_PROXY = ""                   # 代理地址，为空则不使用
DEFAULT_CACHE_TTL = 10               # 分钟
DEFAULT_INCLUDE_STADIUM = False      # 默认不包含 Stadium

# ---------- 状态文件 ----------

STATE_FILENAME = "state.json"
PLUGIN_DATA_DIR_NAME = "astrbot_plugin_owpatch"
