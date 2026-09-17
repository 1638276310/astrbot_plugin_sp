# 涩批插件 · AstrBot Python 版

把原 TRSS-Yunzai / Miao-Yunzai 的 **sp-plugin（涩批插件）** 完整重构为 AstrBot 的 Python 插件。

> **最大特点：每个功能都是独立的 Python 文件**，`main.py` 只负责组装，不再把所有逻辑堆在一个文件里。

* 原插件作者：**寂寞沙洲冷**（QV：1638276310）
* 本重构版作者：寂寞沙洲冷
* 插件标识：`astrbot_plugin_sp`
* 数据目录：`AstrBot/data/plugin_data/astrbot_plugin_sp/`

---

## 一、目录结构

```
涩批插件AstrBotPython版/
├── main.py                       # ⭐ 插件入口（只做组装，不写业务逻辑）
├── metadata.yaml                 # AstrBot 插件元数据
├── _conf_schema.json             # 插件配置项（WebUI 可视化编辑）
├── requirements.txt              # 依赖
├── README.md
├── assets/
│   └── 说明.txt                  # 放 help.jpg 即可使用 /涩批图片帮助
├── app_core/                         # 公共能力（一个文件一件事）
│   ├── paths.py                  # 数据/临时/资源目录
│   ├── settings.py               # 配置读取 + 上游接口与常量
│   ├── storage.py                # JSON 持久化
│   ├── http.py                   # aiohttp 异步请求封装
│   ├── browser.py                # Playwright 无头浏览器封装
│   ├── imaging.py                # 噪点处理 / base64 / 扩展名识别
│   ├── filecache.py              # 图片下载缓存
│   ├── message.py                # 消息发送、合并转发、临时文件、权限
│   ├── scheduler.py              # 内置定时任务调度器
│   ├── pixiv.py                  # P 站接口客户端（含订阅查询）
│   ├── verify.py                 # 验车（磁力详情）
│   ├── magnetcat.py              # 磁力猫搜索
│   ├── mzt.py                    # 妹子图解析
│   └── mtb.py                    # 美图吧（ku1373）解析
└── features/                          # 🎯 功能模块：一个功能一个文件
    ├── _base.py                  # 各功能模块的基类
    ├── _pixiv_base.py            # P 站功能公共逻辑
    ├── plugin.py                 # 把下面这些功能拼成插件类
    ├── help.py                   # 帮助
    ├── recall.py                 # 撤回 / R18 / 图片偏好 / 状态
    ├── urls.py                   # 网址导航
    ├── sj.py                     # 随机短视频
    ├── pixiv_pid.py              # /pid
    ├── pixiv_artist.py           # /随机X张Y作品
    ├── pixiv_tag.py              # /来X张XX图
    ├── mzt.py                    # 妹子图
    ├── mtb.py                    # 美图吧套图
    ├── magnet.py                 # 磁力猫 / 验车
    ├── cos_images.py             # 2图 / 3图
    └── subscribe.py              # 画师订阅与推送
```

**新增一个功能怎么做？**
在 `features/` 下新建一个 `.py`，写一个继承 `spFeature` 的类，用 `@filter.regex(...)` 注册指令，
然后把它加进 `features/__init__.py` 与 `features/plugin.py` 的继承列表即可，完全不用动其他文件。

---

## 二、安装

### 1. 放好插件

把整个 `涩批插件AstrBotPython版` 文件夹（建议重命名为 `astrbot_plugin_sp`）放进：

```
AstrBot/data/plugins/astrbot_plugin_sp/
```

### 2. 安装依赖

AstrBot 会自动读取 `requirements.txt` 安装依赖；也可以手动执行：

```bash
pip install aiohttp pyyaml playwright Pillow
```

### 3. 安装浏览器内核（**磁力猫 / 验车 / 妹子图 / 美图吧 必须**）

这几个功能需要抓取网页，依赖 Playwright：

```bash
playwright install chromium
```

> 如果只使用 P 站图片、网址导航、2图/3图、视频、订阅推送，可以不装浏览器内核。
> 用到爬网页的指令时，如果缺少内核，插件会返回明确的提示而不会崩溃。

### 4. 重载插件

打开 AstrBot WebUI → 插件管理 → 找到「涩批」→ 点击重载。

### 5. 可选：放帮助图

把原插件的 `config/help.jpg` 复制成 `assets/help.jpg`，
这样 `/涩批图片帮助` 会直接发这张图；不放则会自动用 HTML 渲染文字帮助图。

---

## 三、指令一览

> AstrBot 中唤醒前缀默认是 `/`。本插件的指令使用**正则匹配**，
> 所有指令都以 `/` 开头（例如 `/涩批文字帮助`、`/pid123456`、`/骚鸡`），
> 群聊里 @机器人 或消息以 `/` 开头都能触发，私聊直接发也能触发。

### 帮助

| 指令 | 说明 |
| --- | --- |
| `涩批文字帮助` / `sp文字帮助` | 发送文字版帮助 |
| `涩批图片帮助` / `sp图片帮助` | 发送帮助图 |

### 网址导航（`features/urls.py`）

| 指令 | 说明 |
| --- | --- |
| `写真网址` / `福利网址` / `吃瓜网址` / `导航网址` | 获取对应分类的网址列表 |
| `福利App` | 获取福利 App 下载链接 |
| `TG电报` | 获取 TG 频道列表 |

网址内容可在 WebUI 配置的 `url_groups` 中增删。

### 妹子图（`features/mzt.py`）

| 指令 | 说明 |
| --- | --- |
| `写真馆12345` | 按 ID 获取写真（最多 20 张） |
| `随机写真` | 从已保存的 ID 里随机取一个 |
| `更新写真ID` | 增量更新 ID 列表（**仅主人可用**） |

### P 站（`features/pixiv_pid.py` / `pixiv_artist.py` / `pixiv_tag.py`）

| 指令 | 说明 |
| --- | --- |
| `pid123456` | 获取单张作品的全部图片 |
| `随机3张123456作品` | 随机取该画师 3 张作品（张数 ≤ 20） |
| `来10张白丝图` | 按标签搜索 10 张图（数量 ≤ 60） |

`来X张XX图` 受 `/设置R18模式` 与 `/设置图片偏好` 影响。

### 美图吧 / 套图（`features/mtb.py`）

| 指令 | 说明 |
| --- | --- |
| `随机美图吧` | 从已保存套图列表随机抽一套并发送全部图片 |
| `套图详情 https://www.ku1373.cc/xxx/1.html` | 解析指定套图 |
| `更新套图列表` | 增量采集最新 2 页（**仅主人可用**） |
| `全量更新套图列表` | 从第 1 页采到最后一页（**仅主人可用**） |

### 磁力（`features/magnet.py`）

| 指令 | 说明 |
| --- | --- |
| `磁力猫 关键词` | 搜索磁力（默认 10 条） |
| `磁力猫 关键词 影视 热度 5` | 带类型 / 排序 / 数量 |
| `验车 magnet:?xt=urn:btih:...` | 查询磁力详情并发送截图 |

文件类型：`全部` `影视` `音乐` `图像` `文档` `压缩包` `安装包` `其他`
排序方式：`相关度` `文件大小` `添加时间` `热度` `最近下载`

### Cos 图 与 视频（`features/cos_images.py` / `features/sj.py`）

| 指令 | 说明 |
| --- | --- |
| `2图` | 10 张二次元图 |
| `3图` | 10 张三次元图 |
| `骚鸡` / `烧鸡` / `sj` | 随机发送一个视频 |

### 订阅与推送（`features/subscribe.py`）

| 指令 | 说明 |
| --- | --- |
| `订阅画师123456` | 订阅画师更新 |
| `取消订阅123456` | 取消订阅 |
| `订阅列表` | 查看本会话订阅的画师 |
| `sp推送` / `关闭sp推送` | 开启 / 关闭本会话的推送 |
| `sp状态` | 查看撤回、R18、定时任务等运行状态 |

> 群聊与私聊都支持订阅（原插件只支持群聊）。

### 设置（`features/recall.py`，**仅主人可用**）

| 指令 | 说明 |
| --- | --- |
| `开启sp撤回` / `关闭sp撤回` | 撤回开关 |
| `设置sp撤回60` | 设置撤回时间（10 ~ 120 秒） |
| `设置R18模式2` | 0 全部 / 1 非R18 / 2 R18 |
| `设置图片偏好1` | 0 无偏好 / 1 男性 / 2 女性 |

---

## 四、配置项（WebUI 可视化编辑）

打开 AstrBot WebUI → 插件管理 → 涩批 → 配置。常用项：

| 配置项 | 默认值 | 说明 |
| --- | --- | --- |
| `pixiv_api_base` | `https://pid.kkndp.cn` | P 站接口地址，失效时在此更换 |
| `dingyue_api` / `dingyue_key` | `https://user.kkndp.cn` / 空 | 订阅推送接口与 key |
| `magnet_api` | `https://whatslink.info/api/v1/link` | 验车接口 |
| `magnet_cat_sites` | 4 个镜像站 | 磁力猫镜像，失效时可自行更换 |
| `mzt_site` | `https://kkmzt.com` | 妹子图站点 |
| `mtb_site` | `https://www.ku1373.cc` | 美图吧站点 |
| `cos_api` | `https://img.mengxix.top/` | 2图/3图接口 |
| `url_groups` | 6 组网址 | 网址导航内容 |
| `recall` / `recall_time` | `true` / `40` | 撤回开关与秒数 |
| `r18_mode` / `image_preference` | `all` / `popular_d` | 搜索过滤 |
| `track_pixel` | `true` | 给图片叠加噪点（等价原 sharp-pixel） |
| `max_concurrent_download` | `5` | 图片下载并发 |
| `download_timeout` | `20` | 单张图片下载超时（秒） |
| `batch_size` | `20` | 合并转发每批图片数 |
| `max_pages_per_album` | `20` | 单个套图最多解析页数 |
| `enable_scheduler` | `true` | 是否启用内置定时任务 |
| `subscribe_check_interval` | `4` | 订阅推送检查间隔（小时） |
| `scheduler_mzt_cron` | `07:30` | 写真 ID 自动更新时间 |
| `scheduler_mtb_cron` | `09:30` | 套图列表自动更新时间 |
| `enable_id_whitelist` / `id_whitelist` | `false` / `[]` | 会话白名单 |
| `enable_group_limit` | `true` | 是否限制订阅数量 |
| `max_subscribe_sessions` / `max_artists_per_session` | `5` / `20` | 订阅上限 |
| `forward_as_node` | `true` | 多图是否用合并转发发送 |

**如果某些指令一直提示"未安装 playwright"**：执行 `playwright install chromium` 即可；
**如果磁力猫搜不到东西**：多半是镜像站换了域名，把 `magnet_cat_sites` 换成新域名。

---

## 五、数据存放位置

所有数据都在 `AstrBot/data/plugin_data/astrbot_plugin_sp/`：

| 文件 | 内容 |
| --- | --- |
| `sp_video_urls.json` | 骚鸡视频直链列表（首次运行自动从 assets 释放 3.9w+ 内置网址，避免卡死配置页面且更新不覆盖） |
| `dingyue.json` | 画师订阅数据 |
| `mztids.json` | 妹子图写真 ID 列表 |
| `jg.json` | 美图吧套图 URL 列表 |
| `runtime.json` | 定时任务上次执行时间 |
| `cache/` | 图片下载缓存（可随时删除） |
| `temp/` | 临时图片/视频（插件启动会自动清理 1 小时前的文件） |

R18 模式、图片偏好、撤回设置等直接保存在插件配置中，WebUI 可见。

---

## 六、与原 TRSS-Yunzai 版的对应关系

| 原 JS 文件 | AstrBot 版实现 |
| --- | --- |
| `apps/help.js` | `features/help.py` |
| `apps/recall.js` | `features/recall.py` |
| `apps/multiUrl.js` | `features/urls.py` |
| `apps/sj.js` | `features/sj.py` |
| `apps/pid.js` | `features/pixiv_pid.py` |
| `apps/PixivArtistWorksFetcher.js` | `features/pixiv_artist.py` |
| `apps/tag.js` | `features/pixiv_tag.py` |
| `apps/mzt.js` | `features/mzt.py` + `app_core/mzt.py` |
| `apps/mtb.js` | `features/mtb.py` + `app_core/mtb.py` |
| `apps/MagnetLinkMao.js` | `features/magnet.py` + `app_core/magnetcat.py` |
| `apps/MagnetLinkFetcher.js` | `features/magnet.py` + `app_core/verify.py` |
| `apps/tu.js` | `features/cos_images.py` |
| `apps/dingyue.js` | `features/subscribe.py` |
| `apps/dingyue_Auto_update.js` | `features/subscribe.py` + `app_core/scheduler.py` |
| `apps/sp-update.js` | 由 AstrBot 插件管理负责（无需代码） |
| `config/api.js` | `app_core/settings.py` + `app_core/pixiv.py` |
| `config/recall.yaml` | `_conf_schema.json` 中的 `recall` / `recall_time` / `r18_mode` / `image_preference` |
| `config/dingyue.yaml` | `dingyue.json` |
| `lib/sharp-pixel.js` | `app_core/imaging.py` 的 `add_noise()` |
| `index.js` | `main.py`（已合并 `features/plugin.py` 的全部逻辑，该文件已删除） |

### 有意做出的行为调整

1. **`/更新套图列表` / `/全量更新套图列表`** 结束后会把"本次新增 / 现有总计"一起报出来，便于确认是否真的新增了内容。
2. **`/设置R18模式` / `/设置图片偏好` / `/设置sp撤回X`** 做了取值校验，超出范围会提示而不是直接写入。
3. **推送不再依赖 Yunzai 的 `schedule`**：改为插件内部的 asyncio 调度器，
   订阅检查按 `subscribe_check_interval` 小时执行，写真 ID / 套图列表在设定时间每日执行。
4. **合并转发**：原插件需要针对 NapCat 手写 `send_group_forward_msg`，
   AstrBot 版改用标准消息链（`Node` / `Nodes`），由适配器自动选择群/私聊转发接口，
   同时保留 `forward_as_node=false` 时退化为普通消息发送。
5. **数据格式** 由 YAML 统一改为 JSON，便于和 AstrBot 生态一致。

---

## 七、常见问题

**Q：发送指令没反应？**
A：① 群里需要 @机器人 或消息以 `/` 开头（私聊直接发即可）；
② 检查 WebUI 里插件是否启用、是否加载失败；
③ 检查是否开启了 `enable_id_whitelist`。

**Q：提示"未安装 playwright"？**
A：执行 `playwright install chromium`（或 `python -m playwright install chromium`）。

**Q：图片发不出来 / 提示下载失败？**
A：多为图床风控。可以尝试关闭 `track_pixel`，或调大 `download_timeout`，
或把 `max_concurrent_download` 调小。

**Q：磁力猫搜索失败？**
A：镜像站域名变化很频繁，把 `magnet_cat_sites` 换成当前可用的域名即可。

**Q：P 站接口报错 / 返回格式异常？**
A：`pixiv_api_base` 使用的是第三方接口，接口失效时更换为可用接口。

**Q：订阅推送不生效？**
A：① `dingyue_api` 与 `dingyue_key` 必须正确；
② 需要先 `订阅画师<ID>`，再 `sp推送` 开启；
③ 推送通过 `unified_msg_origin` 主动发消息，部分平台不支持主动消息。

**Q：想只让某个群用这个插件？**
A：打开 `enable_id_whitelist`，把群号填进 `id_whitelist`。

---

## 八、开源协议与致谢

* 本项目由 [TRSS-Yunzai **sp-plugin**](https://gitee.com/1638276310/sp-plugin)（作者：寂寞沙洲冷）重构而来。
* 原项目基于 [所有二刺螈都得死——kkp-plugin](https://gitee.com/dungeonmaster/kkp-plugin) 二次开发，
  原项目采用 **AFL (Academic Free License) 3.0**；sp-plugin 新增部分采用 **MIT**。
* 本重构版：新增的 Python 代码采用 **MIT**，并保留原项目的所有版权声明与归属信息。
* 请在使用、修改、分发时遵守上述协议。

**免责声明**：本插件仅用于技术交流与学习，所有内容来自第三方网站，
请勿用于任何商业用途或违法行为，使用风险自负。
