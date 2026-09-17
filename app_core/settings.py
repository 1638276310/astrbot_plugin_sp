"""插件配置读取。

AstrBot 会把 ``_conf_schema.json`` 解析成 ``AstrBotConfig``（dict 子类）并
在实例化插件时传入，这里把它包装成一个带属性的对象，方便各功能模块读取。
"""

from __future__ import annotations

from typing import Any

PLUGIN_VERSION = "v1.0.0"

# 与原 TRSS 插件 config/api.js 保持一致的上游接口
DEFAULT_PIXIV_API_BASE = "https://pid.kkndp.cn"
DEFAULT_DINGYUE_API = "https://user.kkndp.cn"
DEFAULT_MAGNET_API = "https://whatslink.info/api/v1/link"
DEFAULT_MTB_SITE = "https://www.ku1373.cc"
DEFAULT_MZT_SITE = "https://kkmzt.com"
DEFAULT_COS_API = "https://img.mengxix.top/"

DEFAULT_MAGNET_CAT_SITES = [
    "https://9rpzb3to.8800591.xyz",
    "https://rkvbbvso.8800592.xyz",
    "https://b3tp07ox.8800593.xyz",
    "https://46ne63ox.8800594.xyz",
]

# 网址导航默认分组（与原 multiUrl.js 完全一致）
DEFAULT_URL_GROUPS: dict[str, list[str]] = {
    "写真": [
        "https://kkmzt.com/",
        "https://dimtown.com/",
        "https://shaonvzhi.top/",
        "https://www.chnci.cc/",
        "https://coser01.com/",
        "https://rosi73.com/",
        "https://cosaas.top/",
        "https://jrants.com/",
    ],
    "福利": [
        "https://cn.pornhub.com/",
        "https://www.pixiv.net/",
        "https://javdb.com/",
        "https://xiaoyakankan.com/cat/1307.html",
        "https://www.sex.com/en",
        "https://jappydolls.net/",
        "https://t66y.com/",
    ],
    "吃瓜": [
        "https://www.718yule.com/",
        "https://slush.cncsbb.com/",
        "https://drive.cncsbb.com/",
        "https://fizzy.cncsbb.com/",
        "https://pc001.bkih1ca5.work/",
        "https://hl007.okbktyd8.work/",
        "https://pchl.5r1ilblr.work/",
        "https://51cg1.com/",
        "https://ball.bwljkjmd.com/",
        "https://cake.bwljkjmd.com/",
        "https://account.bwljkjmd.com/",
    ],
    "导航": [
        "https://www.xbookcn.net/",
        "https://tlwblhlc.8800531.xyz/",
        "https://abhwmcxb.8800532.xyz/",
        "https://vuonsksu.8800533.xyz/",
        "https://opryfboh.8800534.xyz/",
        "https://iqzmsssaabt.007home.cc/",
        "https://a09.mpmhskx.cc/c/Csn",
    ],
    "福利App": [
        "https://i.meizitu.net/dl/",
        "https://db1113.7v9h2x7x.work/android/007dh_2.5.0/007dh_2.5.0_04263238.apk",
        "https://da1113.0r6ecdax.work/android/007dh_1.9.1/007dh_1.9.1_04413849.apk",
        "https://df1113.qmpqcv83.work/android/007dh_2.0.7/007dh_2.0.7_04018311.apk",
    ],
    "TG电报": [
        "https://t.me/bkyss233",
        "https://t.me/shaoluo1112",
        "https://t.me/SoShaoNv",
        "https://t.me/fulj10",
        "https://t.me/fuli366",
        "https://t.me/whdq8",
        "https://t.me/sheshewu",
        "https://t.me/dyttmg",
    ],
}

# 网址分组对应的指令触发词 -> 分组名
URL_GROUP_ALIASES: dict[str, str] = {
    "写真网址": "写真",
    "福利网址": "福利",
    "吃瓜网址": "吃瓜",
    "导航网址": "导航",
    "福利App": "福利App",
    "福利APP": "福利App",
    "福利app": "福利App",
    "TG电报": "TG电报",
}

# 骚鸡视频直链已按照 AstrBot 规范统一迁移至 data/plugin_data/<plugin_name>/sp_video_urls.json 持久化存储
# 避免海量视频直链卡死 WebUI 配置文件，此处保留空列表作为配置项兼容兜底。
DEFAULT_VIDEO_URLS: list[str] = []

# 磁力猫文件类型 / 排序映射（与原 MagnetLinkMao.js 一致）
FILE_TYPE_MAP = {
    "全部": 0,
    "影视": 1,
    "音乐": 2,
    "图像": 3,
    "文档": 4,
    "压缩包": 5,
    "安装包": 6,
    "其他": 7,
}

ORDER_TYPE_MAP = {
    "相关度": 0,
    "文件大小": 1,
    "添加时间": 2,
    "热度": 3,
    "最近下载": 4,
}

# R18 / 图片偏好映射（与原 recall.js 一致）
R18_MODE_MAP = {"0": "all", "1": "safe", "2": "r18"}
R18_MODE_LABEL = {"all": "全部", "safe": "非R18", "r18": "R18"}

ORDER_MAP = {
    "0": "popular_d",
    "1": "popular_male_d",
    "2": "popular_female_d",
}
ORDER_LABEL = {
    "popular_d": "无偏好",
    "popular_male_d": "男性偏好",
    "popular_female_d": "女性偏好",
}


class PluginSettings:
    """插件运行时设置（带属性访问 + 合法值兜底）。"""

    def __init__(self, raw: Any = None) -> None:
        self._raw = raw if isinstance(raw, dict) else {}
        self.pixiv_api_base = self._str("pixiv_api_base", DEFAULT_PIXIV_API_BASE)
        self.dingyue_api = self._str("dingyue_api", DEFAULT_DINGYUE_API)
        self.dingyue_key = self._str("dingyue_key", "")
        self.magnet_api = self._str("magnet_api", DEFAULT_MAGNET_API)
        self.magnet_cat_sites = self._list("magnet_cat_sites", DEFAULT_MAGNET_CAT_SITES)
        self.mzt_site = self._str("mzt_site", DEFAULT_MZT_SITE).rstrip("/")
        self.mtb_site = self._str("mtb_site", DEFAULT_MTB_SITE).rstrip("/")
        self.cos_api = self._str("cos_api", DEFAULT_COS_API)
        self.sp_video_urls = self._list("sp_video_urls", DEFAULT_VIDEO_URLS)

        groups = self._raw.get("url_groups")
        self.url_groups: dict[str, list[str]] = {}
        if isinstance(groups, dict):
            for key, value in groups.items():
                if isinstance(value, list) and value:
                    self.url_groups[str(key)] = [str(item) for item in value]
        if not self.url_groups:
            self.url_groups = {k: list(v) for k, v in DEFAULT_URL_GROUPS.items()}

        self.track_pixel = self._bool("track_pixel", True)
        self.max_concurrent_download = max(1, self._int("max_concurrent_download", 5))
        self.download_timeout = max(5, self._int("download_timeout", 20))
        self.batch_size = max(1, self._int("batch_size", 20))
        self.max_pages_per_album = max(1, self._int("max_pages_per_album", 20))
        self.random_interval = max(0, self._int("random_interval", 2))

        self.subscribe_check_interval = max(1, self._int("subscribe_check_interval", 4))
        self.enable_scheduler = self._bool("enable_scheduler", True)
        self.scheduler_mzt_cron = self._str("scheduler_mzt_cron", "07:30")
        self.scheduler_mtb_cron = self._str("scheduler_mtb_cron", "09:30")

        self.enable_id_whitelist = self._bool("enable_id_whitelist", False)
        self.id_whitelist = [str(x) for x in self._list("id_whitelist", [])]
        self.enable_group_limit = self._bool("enable_group_limit", True)
        self.max_subscribe_sessions = max(1, self._int("max_subscribe_sessions", 5))
        self.max_artists_per_session = max(
            1, self._int("max_artists_per_session", 20)
        )
        self.forward_as_node = self._bool("forward_as_node", True)

    # ------------------------------------------------------------------ #
    # 读取辅助
    # ------------------------------------------------------------------ #
    def _str(self, key: str, default: str) -> str:
        value = self._raw.get(key, default)
        if value is None:
            return default
        text = str(value).strip()
        return text or default

    def _int(self, key: str, default: int) -> int:
        try:
            return int(self._raw.get(key, default))
        except (TypeError, ValueError):
            return default

    def _bool(self, key: str, default: bool) -> bool:
        value = self._raw.get(key, default)
        if isinstance(value, bool):
            return value
        if isinstance(value, str):
            return value.strip().lower() in {"1", "true", "yes", "on"}
        return bool(value)

    def _list(self, key: str, default: list) -> list:
        value = self._raw.get(key, default)
        if isinstance(value, list):
            return list(value)
        if isinstance(value, str) and value.strip():
            return [item.strip() for item in value.split(",") if item.strip()]
        return list(default)

    # ------------------------------------------------------------------ #
    # 撤回 / 设置项（读写均落到 AstrBotConfig，可持久化到配置文件）
    # ------------------------------------------------------------------ #
    @property
    def recall(self) -> bool:
        return self._bool("recall", True)

    @recall.setter
    def recall(self, value: bool) -> None:
        self._raw["recall"] = bool(value)
        self._save()

    @property
    def recall_time(self) -> int:
        seconds = self._int("recall_time", 40)
        return min(120, max(10, seconds))

    @recall_time.setter
    def recall_time(self, seconds: int) -> None:
        self._raw["recall_time"] = int(seconds)
        self._save()

    @property
    def r18_mode(self) -> str:
        mode = str(self._raw.get("r18_mode", "all")).strip()
        return mode if mode in {"all", "safe", "r18"} else "all"

    @r18_mode.setter
    def r18_mode(self, mode: str) -> None:
        self._raw["r18_mode"] = mode
        self._save()

    @property
    def image_preference(self) -> str:
        order = str(self._raw.get("image_preference", "popular_d")).strip()
        return (
            order
            if order in {"popular_d", "popular_male_d", "popular_female_d"}
            else "popular_d"
        )

    @image_preference.setter
    def image_preference(self, order: str) -> None:
        self._raw["image_preference"] = order
        self._save()

    def _save(self) -> None:
        save = getattr(self._raw, "save_config", None)
        if callable(save):
            try:
                save()
            except Exception:
                pass

    def url_group(self, name: str) -> list[str]:
        return list(self.url_groups.get(name, []))

    @property
    def group_names(self) -> list[str]:
        return list(self.url_groups.keys())


def build_settings(raw: Any = None) -> PluginSettings:
    """根据 AstrBotConfig 构造设置对象。"""
    return PluginSettings(raw)
