"""涩批插件 AstrBot 版 —— 插件入口。

AstrBot 只要求插件类所在的文件名为 ``main.py``。
本文件不做任何功能实现，只负责把 ``cmd/`` 目录下按功能拆分的各个模块
组装成最终的插件类，真正的指令实现请看：

* ``cmd/help.py``          帮助
* ``cmd/recall.py``        撤回 / R18 / 图片偏好 / 状态
* ``cmd/urls.py``          网址导航
* ``cmd/sj.py``            随机短视频
* ``cmd/pixiv_pid.py``     #pid
* ``cmd/pixiv_artist.py``  #随机X张Y作品
* ``cmd/pixiv_tag.py``     #来X张XX图
* ``cmd/mzt.py``           妹子图
* ``cmd/mtb.py``           美图吧套图
* ``cmd/magnet.py``        磁力猫 / 验车
* ``cmd/cos_images.py``    2图 / 3图
* ``cmd/subscribe.py``     画师订阅与推送
* ``core/``                公共能力（HTTP / 图片 / 浏览器 / 存储 / 配置 …）
"""

from .cmd.plugin import spPlugin

__all__ = ["spPlugin"]
