"""插件配置读取。

AstrBot 会把 ``_conf_schema.json`` 解析成 ``AstrBotConfig``（dict 子类）并
在实例化插件时传入，这里把它包装成一个带属性的对象，方便各功能模块读取。
"""

from __future__ import annotations

import os
from typing import Any

PLUGIN_VERSION = "v1.0.0"

# --------------------------------------------------------------------------- #
# 妹子图 APP 链路：内置默认值（用户无需在 WebUI 填写任何一项）
#
# 2026-10 起以下配置从 _conf_schema.json 移除，全部改为代码常量：
#   * APP 接口基址（官方 APP 3.8.7 逆向所得，固定为 api.iimzt.com/app/）
#   * 是否取高清原图（默认关闭：全量普通图已经够用，高清流量更大）
#   * mTLS 客户端证书（随插件源码附带 assets/mzt_client.pem）
#
# 出站请求默认使用插件所在设备的本机 IP 直连，不做自动代理回退；
# 只有显式设置 SP_MZT_PROXY 时才把妹子图链路切到指定 HTTP(S) CONNECT 代理，
# 便于服务器出口 IP 被 Cloudflare 风控时复用干净的代理/隧道出口。
# 代理地址只从环境变量读取，不写入 WebUI、不打印账号密码，保证默认行为可复核。
# 保留“可覆盖入口”但不作为必填项：仅支持环境变量（见 MZT_ENV_* 常量），
# 不再读取旧 WebUI 配置值，避免历史填错/留空继续影响功能。
# --------------------------------------------------------------------------- #
MZT_APP_API_DEFAULT = "https://api.iimzt.com/app/"
MZT_HD_DEFAULT = False
MZT_APP_CERT_DEFAULT = "assets/mzt_client.pem"

# 主用环境变量名（兼容不带 SP_ 前缀的旧诊断脚本习惯）
MZT_ENV_APP_API = "SP_MZT_APP_API"
MZT_ENV_HD = "SP_MZT_HD"
MZT_ENV_APP_CERT = "SP_MZT_APP_CERT"
MZT_ENV_PROXY = "SP_MZT_PROXY"
MTB_ENV_PROXY = "SP_MTB_PROXY"
MZT_ENV_APP_API_COMPAT = "MZT_APP_API"
MZT_ENV_HD_COMPAT = "MZT_HD"
MZT_ENV_APP_CERT_COMPAT = "MZT_APP_CERT"
MZT_ENV_PROXY_COMPAT = "MZT_PROXY"

# 旧 WebUI 配置键：只用于日志提示“已忽略”，不参与运行
MZT_LEGACY_CONFIG_KEYS = ("mzt_app_api", "mzt_hd", "mzt_proxy", "mzt_app_cert")


def _env_text(keys: tuple[str, ...], default: str) -> str:
    """按顺序取第一个非空环境变量；都没有则返回内置默认值。"""
    for key in keys:
        value = str(os.environ.get(key) or "").strip()
        if value:
            return value
    return default


def _env_bool(keys: tuple[str, ...], default: bool) -> bool:
    """环境变量布尔值：1/true/yes/on 为真，其余非空值为假。"""
    for key in keys:
        raw = str(os.environ.get(key) or "").strip()
        if raw:
            return raw.lower() in {"1", "true", "yes", "on"}
    return default

# 与原 TRSS 插件 config/api.js 保持一致的上游接口
DEFAULT_PIXIV_API_BASE = "https://pid.kkndp.cn"
DEFAULT_DINGYUE_API = "https://user.kkndp.cn"
DEFAULT_MAGNET_API = "https://whatslink.info/api/v1/link"
DEFAULT_MTB_SITE = "https://www.ku1373.cc"
DEFAULT_MZT_SITE = "https://kkmzt.com"
DEFAULT_COS_API = "https://img.mengxix.top/"

DEFAULT_MAGNET_CAT_SITES = [
    # 2026-10 最新域名；失效时会按顺序回退到下面的历史镜像
      "https://rt51whj5.8800591.xyz",
      "https://gg59os5i.8800592.xyz",
      "https://9lxirte0.8800593.xyz",
      "https://lv6mll9s.8800594.xyz"
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
        # 以下 4 项不再从 WebUI 读取：内置默认值 + 可选环境变量覆盖。
        self.mzt_app_api = _env_text(
            (MZT_ENV_APP_API, MZT_ENV_APP_API_COMPAT), MZT_APP_API_DEFAULT
        ).rstrip("/")
        self.mzt_hd = _env_bool((MZT_ENV_HD, MZT_ENV_HD_COMPAT), MZT_HD_DEFAULT)
        # 0 = 不限制（抓取接口能返回的全部图片）；>0 时只取前 N 张
        self.mzt_max_images = max(0, self._int("mzt_max_images", 0))
        # mTLS 客户端证书：内置随包相对路径；环境变量可覆盖（仍只接受插件内相对路径）。
        self.mzt_app_cert = _env_text(
            (MZT_ENV_APP_CERT, MZT_ENV_APP_CERT_COMPAT), MZT_APP_CERT_DEFAULT
        )
        # 可选显式代理（默认空 = 直连）。只接受 http(s)://host:port，
        # 不读取 HTTP_PROXY/ALL_PROXY，也不做“失败后自动换代理”。
        self.mzt_proxy = _env_text((MZT_ENV_PROXY, MZT_ENV_PROXY_COMPAT), "")
        # 美图吧浏览器代理；未单独设置时复用妹图代理，方便一条隧道覆盖两条链路。
        self.mtb_proxy = _env_text((MTB_ENV_PROXY,), "") or self.mzt_proxy
        # 旧 WebUI 键若仍残留在配置里，仅记录一次“已忽略”，不参与运行。
        self.legacy_mzt_keys = [
            key for key in MZT_LEGACY_CONFIG_KEYS if key in self._raw
        ]
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
        """撤回开关（持久化到插件配置）。"""
        return self._bool("recall", True)

    @recall.setter
    def recall(self, value: bool) -> None:
        """设置撤回开关（持久化到插件配置）。"""
        self._raw["recall"] = bool(value)
        self._save()

    @property
    def recall_time(self) -> int:
        """撤回等待时间（秒，限制在 10~120）。"""
        seconds = self._int("recall_time", 40)
        return min(120, max(10, seconds))

    @recall_time.setter
    def recall_time(self, seconds: int) -> None:
        """设置撤回等待时间（秒，持久化到插件配置）。"""
        self._raw["recall_time"] = int(seconds)
        self._save()

    @property
    def r18_mode(self) -> str:
        """R18 内容过滤模式（all/safe/r18，非法值回退 all）。"""
        mode = str(self._raw.get("r18_mode", "all")).strip()
        return mode if mode in {"all", "safe", "r18"} else "all"

    @r18_mode.setter
    def r18_mode(self, mode: str) -> None:
        """设置 R18 内容过滤模式（持久化到插件配置）。"""
        self._raw["r18_mode"] = mode
        self._save()

    @property
    def image_preference(self) -> str:
        """图片排序偏好（非法值回退 popular_d）。"""
        order = str(self._raw.get("image_preference", "popular_d")).strip()
        return (
            order
            if order in {"popular_d", "popular_male_d", "popular_female_d"}
            else "popular_d"
        )

    @image_preference.setter
    def image_preference(self, order: str) -> None:
        """设置图片排序偏好（持久化到插件配置）。"""
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
        """按名称读取一组 URL 列表。"""
        return list(self.url_groups.get(name, []))

    @property
    def group_names(self) -> list[str]:
        """全部 URL 分组名称。"""
        return list(self.url_groups.keys())


def build_settings(raw: Any = None) -> PluginSettings:
    """根据 AstrBotConfig 构造设置对象。"""
    return PluginSettings(raw)
