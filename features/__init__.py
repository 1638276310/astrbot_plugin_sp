"""涩批插件 AstrBot 版 —— 功能模块汇总。

结构说明（与 get_px 同构）：
* 全部 28 个 ``@filter.command`` 指令 handler 注册在 ``main.py`` 的 ``spPlugin`` 主类上；
* 本目录下的各 Mixin 类只保留**业务方法**（数据读写、解析、下载、推送等），
  不再注册任何指令，避免同一指令被重复注册造成双触发。

各模块职责：

* ``features/help.py``        帮助图渲染（_send_help_image）
* ``features/recall.py``      撤回/R18/偏好的解析正则
* ``features/urls.py``        网址分组解析（_resolve_group）
* ``features/sj.py``          视频列表读写
* ``features/pixiv_pid.py``   pid 解析正则
* ``features/pixiv_artist.py`` 画师作品解析正则
* ``features/pixiv_tag.py``   标签解析正则
* ``features/mzt.py``         妹子图 ID 存取与写真发送
* ``features/mtb.py``         美图吧套图采集与发送
* ``features/magnet.py``      磁力相关常量
* ``features/cos_images.py``  2图/3图 常量映射
* ``features/subscribe.py``   画师订阅存储与定时推送
"""

from .cos_images import CosImageFeature
from .help import HelpFeature
from .magnet import MagnetFeature
from .mtb import MtbFeature
from .mzt import MztFeature
from .pixiv_artist import PixivArtistFeature
from .pixiv_pid import PixivPidFeature
from .pixiv_tag import PixivTagFeature
from .recall import RecallFeature
from .sj import VideoFeature
from .subscribe import SubscribeFeature
from .urls import UrlFeature

__all__ = [
    "CosImageFeature",
    "HelpFeature",
    "MagnetFeature",
    "MtbFeature",
    "MztFeature",
    "PixivArtistFeature",
    "PixivPidFeature",
    "PixivTagFeature",
    "RecallFeature",
    "SubscribeFeature",
    "UrlFeature",
    "VideoFeature",
]
