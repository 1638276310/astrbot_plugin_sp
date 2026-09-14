"""涩批插件 AstrBot 版 —— 插件入口。

AstrBot 只要求插件类所在的文件名为 ``main.py``。
本文件不做任何功能实现，只负责把 ``features/`` 目录下按功能拆分的各个模块
组装成最终的插件类，真正的指令实现请看：

* ``features/help.py``          帮助
* ``features/recall.py``        撤回 / R18 / 图片偏好 / 状态
* ``features/urls.py``          网址导航
* ``features/sj.py``            随机短视频
* ``features/pixiv_pid.py``     #pid
* ``features/pixiv_artist.py``  #随机X张Y作品
* ``features/pixiv_tag.py``     #来X张XX图
* ``features/mzt.py``           妹子图
* ``features/mtb.py``           美图吧套图
* ``features/magnet.py``        磁力猫 / 验车
* ``features/cos_images.py``    2图 / 3图
* ``features/subscribe.py``     画师订阅与推送
* ``app_core/``                公共能力（HTTP / 图片 / 浏览器 / 存储 / 配置 …）
"""

from .features.plugin import spPlugin

__all__ = ["spPlugin"]
