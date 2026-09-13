"""涩批插件 AstrBot 版 —— 功能模块汇总。

每个功能都是独立文件，这里只负责把它们拼装成最终的插件类：

* ``cmd/help.py``        帮助
* ``cmd/recall.py``      撤回 / R18 / 图片偏好 / 状态
* ``cmd/urls.py``        网址导航
* ``cmd/sj.py``          短视频
* ``cmd/pixiv_pid.py``   #pid
* ``cmd/pixiv_artist.py``#随机X张Y作品
* ``cmd/pixiv_tag.py``   #来X张XX图
* ``cmd/mzt.py``         妹子图
* ``cmd/mtb.py``         美图吧套图
* ``cmd/magnet.py``      磁力猫 / 验车
* ``cmd/cos_images.py``  2图 / 3图
* ``cmd/subscribe.py``   画师订阅与推送
"""

from cmd.cos_images import CosImageFeature
from cmd.help import HelpFeature
from cmd.magnet import MagnetFeature
from cmd.mtb import MtbFeature
from cmd.mzt import MztFeature
from cmd.pixiv_artist import PixivArtistFeature
from cmd.pixiv_pid import PixivPidFeature
from cmd.pixiv_tag import PixivTagFeature
from cmd.recall import RecallFeature
from cmd.sj import VideoFeature
from cmd.subscribe import SubscribeFeature
from cmd.urls import UrlFeature

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
