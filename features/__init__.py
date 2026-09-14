"""涩批插件 AstrBot 版 —— 功能模块汇总。

每个功能都是独立文件，这里只负责把它们拼装成最终的插件类：

* ``features/help.py``        帮助
* ``features/recall.py``      撤回 / R18 / 图片偏好 / 状态
* ``features/urls.py``        网址导航
* ``features/sj.py``          短视频
* ``features/pixiv_pid.py``   #pid
* ``features/pixiv_artist.py``#随机X张Y作品
* ``features/pixiv_tag.py``   #来X张XX图
* ``features/mzt.py``         妹子图
* ``features/mtb.py``         美图吧套图
* ``features/magnet.py``      磁力猫 / 验车
* ``features/cos_images.py``  2图 / 3图
* ``features/subscribe.py``   画师订阅与推送
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
