"""
状态管理器 — JSON 文件持久化，记录已推送补丁、绑定 UMO 等
数据存储于 AstrBot 的 data/plugin_data/astrbot_plugin_owpatch/ 目录
"""

import json
import os
from pathlib import Path

from astrbot.api import logger
from astrbot.core.utils.astrbot_path import get_astrbot_data_path

from .config import STATE_FILENAME, PLUGIN_DATA_DIR_NAME


class StateManager:
    """管理插件持久化状态（JSON 文件）。"""

    def __init__(self):
        self._data_dir: Path | None = None
        self._state_path: Path | None = None
        self._data: dict = {}

    # ------------------------------------------------------------------
    # 初始化
    # ------------------------------------------------------------------

    def init_data_dir(self) -> None:
        """初始化数据目录路径（依赖 AstrBot 运行时环境）。"""
        if self._data_dir is not None:
            return
        try:
            astrbot_data = Path(get_astrbot_data_path())
            self._data_dir = astrbot_data / "plugin_data" / PLUGIN_DATA_DIR_NAME
            self._data_dir.mkdir(parents=True, exist_ok=True)
            self._state_path = self._data_dir / STATE_FILENAME
            logger.info(f"[state] 数据目录: {self._data_dir}")
        except Exception as e:
            logger.error(f"[state] 初始化数据目录失败: {e}")
            # 回退到插件目录
            self._data_dir = Path(__file__).parent
            self._state_path = self._data_dir / STATE_FILENAME

    @property
    def data_dir(self) -> Path | None:
        return self._data_dir

    # ------------------------------------------------------------------
    # 加载 / 保存
    # ------------------------------------------------------------------

    def load(self) -> dict:
        """从 JSON 文件加载状态。文件不存在时返回默认值。"""
        if self._state_path is None:
            self.init_data_dir()
        try:
            if self._state_path and self._state_path.exists():
                with open(self._state_path, "r", encoding="utf-8") as f:
                    self._data = json.load(f)
                logger.info(f"[state] 已加载状态文件: {self._state_path}")
            else:
                self._data = self._default_state()
                logger.info("[state] 状态文件不存在，使用默认值")
        except (json.JSONDecodeError, IOError) as e:
            logger.error(f"[state] 加载状态文件失败: {e}，使用默认值")
            self._data = self._default_state()
        return self._data

    def save(self) -> None:
        """将当前状态写入 JSON 文件。"""
        if self._state_path is None:
            self.init_data_dir()
        try:
            if self._state_path:
                with open(self._state_path, "w", encoding="utf-8") as f:
                    json.dump(self._data, f, ensure_ascii=False, indent=2)
                logger.debug("[state] 状态已保存")
        except IOError as e:
            logger.error(f"[state] 保存状态文件失败: {e}")

    # ------------------------------------------------------------------
    # 补丁追踪
    # ------------------------------------------------------------------

    def is_new_patch(self, latest_date: str, latest_hash: str) -> bool:
        """判断是否为新补丁（与本地记录比较）。"""
        recorded_date = self._data.get("last_patch_date", "")
        recorded_hash = self._data.get("last_patch_hash", "")

        # 日期已更新或内容哈希已变化 → 新补丁
        if latest_date and latest_date != recorded_date:
            return True
        if latest_hash and latest_hash != recorded_hash:
            return True
        return False

    def mark_pushed(self, date: str, content_hash: str, section_hashes: dict | None = None) -> None:
        """记录已推送的最新补丁信息。"""
        self._data["last_patch_date"] = date
        self._data["last_patch_hash"] = content_hash
        if section_hashes is not None:
            self._data["section_hashes"] = section_hashes
        self._data["today_pushed"] = True
        self._data["last_push_time"] = self._now_iso()
        self.save()

    def get_last_patch_date(self) -> str:
        """获取本地记录的最新补丁日期。"""
        return self._data.get("last_patch_date", "")

    def set_baseline(self, date: str, content_hash: str, section_hashes: dict | None = None) -> None:
        """首次安装时静默记录基线，不标记为已推送。"""
        self._data["last_patch_date"] = date
        self._data["last_patch_hash"] = content_hash
        if section_hashes is not None:
            self._data["section_hashes"] = section_hashes
        self._data["today_pushed"] = False
        self.save()
        logger.info(f"[state] 基线已记录: {date}")

    def get_section_hashes(self) -> dict:
        """获取已记录的各章节哈希。"""
        return self._data.get("section_hashes", {})

    def find_delta_sections(self, current_hashes: dict) -> list[str]:
        """找出新增或内容变更的章节 heading。"""
        recorded = self.get_section_hashes()
        delta = []
        for heading, h in current_hashes.items():
            if heading not in recorded or recorded[heading] != h:
                delta.append(heading)
        return delta

    def find_deleted_sections(self, current_hashes: dict) -> list[str]:
        """找出已从在线版消失的章节 heading（旧状态中有，当前没有）。"""
        recorded = self.get_section_hashes()
        deleted = []
        for heading in recorded:
            if heading not in current_hashes:
                deleted.append(heading)
        return deleted

    def find_all_deltas(self, current_hashes: dict) -> tuple[list[str], list[str]]:
        """统一查询新增／修改章节 和 已删除章节。

        Returns:
            (changed_headings, deleted_headings)
        """
        changed = self.find_delta_sections(current_hashes)
        deleted = self.find_deleted_sections(current_hashes)
        return changed, deleted

    # ------------------------------------------------------------------
    # 当日状态
    # ------------------------------------------------------------------

    @property
    def today_pushed(self) -> bool:
        return self._data.get("today_pushed", False)

    def reset_daily_if_new_day(self, today_str: str) -> bool:
        """跨天时重置当日推送标记。

        Args:
            today_str: 当前日期字符串（YYYY-MM-DD）

        Returns:
            是否发生了重置（即进入了新的一天）
        """
        recorded = self._data.get("today_date", "")
        if recorded != today_str:
            self._data["today_pushed"] = False
            self._data["today_date"] = today_str
            self.save()
            logger.info(f"[state] 跨天重置: {recorded} → {today_str}")
            return True
        return False

    # ------------------------------------------------------------------
    # UMO 管理
    # ------------------------------------------------------------------

    def add_umo(self, umo: str) -> bool:
        """添加绑定的 UMO。已存在则返回 False。"""
        umos: list = self._data.setdefault("bound_umos", [])
        if umo not in umos:
            umos.append(umo)
            self.save()
            logger.info(f"[state] 绑定 UMO: {umo}")
            return True
        return False

    def remove_umo(self, umo: str) -> bool:
        """移除绑定的 UMO。不存在则返回 False。"""
        umos: list = self._data.get("bound_umos", [])
        if umo in umos:
            umos.remove(umo)
            self.save()
            logger.info(f"[state] 解绑 UMO: {umo}")
            return True
        return False

    def get_umos(self) -> list[str]:
        """获取所有绑定的 UMO 列表。"""
        return self._data.get("bound_umos", [])

    def umo_count(self) -> int:
        """获取绑定的 UMO 数量。"""
        return len(self.get_umos())

    # ------------------------------------------------------------------
    # 私有辅助
    # ------------------------------------------------------------------

    @staticmethod
    def _default_state() -> dict:
        return {
            "last_patch_date": "",
            "last_patch_hash": "",
            "bound_umos": [],
            "today_pushed": False,
            "today_date": "",
            "last_push_time": None,
            "last_check_time": None,
        }

    @staticmethod
    def _now_iso() -> str:
        from datetime import datetime, timezone, timedelta
        return datetime.now(timezone(timedelta(hours=8))).isoformat()
