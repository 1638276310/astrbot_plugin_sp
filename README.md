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
│   ├── help.png                  # /涩批图片帮助 发送的新版完整帮助长图
│   ├── mzt_client.pem            # APP 接口 mTLS 客户端证书（随仓库附带，克隆即可用）
│   └── 说明.txt                  # 帮助图说明
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
│   ├── mzt.py                    # 妹子图解析（写真馆 / 潮拍馆）
│   ├── mzt_crypto.py             # 妹子图图片接口解密（网页 GCM / APP AES-CBC）
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
    ├── pixiv_tag.py              # /来图 X 张 XX图
    ├── mzt.py                    # 妹子图
    ├── mtb.py                    # 美图吧套图
    ├── magnet.py                 # 磁力猫 / 验车
    ├── cos_images.py             # 2图 / 3图
    └── subscribe.py              # 画师订阅与推送
```

**新增一个功能怎么做？**
在 `features/` 下新建一个 `.py`，写一个继承 `spFeature` 的类，用 `@filter.command("命令名", priority=N)` 注册指令，
然后把它加进 `features/__init__.py` 与 `main.py` 的继承列表即可，完全不用动其他文件。

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

### 3. 安装浏览器内核（**磁力猫 / 验车 / 美图吧 必须，妹子图仅兜底需要**）

这几个功能需要抓取网页，依赖 Playwright：

```bash
playwright install chromium
```

> 妹子图已改为**优先直连 APP/站点接口**（`aiohttp`），只有接口被 Cloudflare 拦截时才会用到
> 浏览器兜底；如果不需要磁力猫 / 验车 / 美图吧，可以不装浏览器内核。
> 用到需要浏览器的指令时，如果缺少内核，插件会返回明确的提示而不会崩溃。

### 4. 重载插件

打开 AstrBot WebUI → 插件管理 → 找到「涩批」→ 点击重载。

### 5. 帮助图

`/涩批图片帮助` 直接发送插件自带的 `assets/help.png`：

- 1200px 宽、暗黑赛博朋克 + 玻璃拟态、双栏 masonry 长图；
- 命令较少的分类会紧接下一张卡片，不留大块空白；
- 想替换时保持文件名为 `help.png` 覆盖即可，不需要改代码。

如果 `help.png` 缺失，插件会自动用 Pillow 渲染兜底；再失败才发送文字版。

---

## 三、指令一览

> AstrBot 中唤醒前缀默认是 `/`。本插件的指令使用 **`filter.command` 注册**，
> 所有指令都以 `/` 开头（例如 `/涩批文字帮助`、`/pid 123456`、`/骚鸡`）。
> 带数字/参数的指令建议**在命令名和参数之间加一个空格**（如 `/写真馆 12345`），
> 这样 AstrBot 会直接识别为命令，不会被 LLM 拦截。
> 群聊里 @机器人 或消息以 `/` 开头都能触发，私聊直接发也能触发。

### 帮助

| 指令 | 说明 |
| --- | --- |
| `/涩批文字帮助` / `/sp文字帮助` | 发送文字版帮助 |
| `/涩批图片帮助` / `/sp图片帮助` | 发送 `assets/help.png` 新版帮助长图 |

### 网址导航（`features/urls.py`）

| 指令 | 说明 |
| --- | --- |
| `/写真网址` / `/福利网址` / `/吃瓜网址` / `/导航网址` | 获取对应分类的网址列表 |
| `/福利App` | 获取福利 App 下载链接 |
| `/TG电报` | 获取 TG 频道列表 |

网址内容可在 WebUI 配置的 `url_groups` 中增删。

### 妹子图（`features/mzt.py`）

| 指令 | 说明 |
| --- | --- |
| `/写真馆 12345` 或 `/写真馆 https://kkmzt.com/photo/12345` | 按 ID / 链接获取写真 |
| `/潮拍馆 12345` 或 `/潮拍馆 https://kkmzt.com/beauty/12345` | 按 ID / 链接获取潮拍馆图集 |
| `/随机写真` | 从已保存的 ID 里随机取一个 |
| `/更新写真ID` | 增量更新 ID 列表（**仅主人可用**） |

> 妹子图现在**优先直连官方 APP 图片接口**（`api.iimzt.com`，mTLS 客户端证书 + 全量图片），
> APP 接口不可用时自动回退网页接口，再失败才回退 Playwright 浏览器。
> 网页接口下潮拍馆（`/beauty/`）为全量；写真馆（`/photo/`）被站点限制为
> **前 20 张预览**（插件会如实标注 `20/57` 这类数量）。
> 详细逆向说明见下方 [「妹子图图片接口逆向说明」](#妹子图图片接口逆向说明)。

### P 站（`features/pixiv_pid.py` / `pixiv_artist.py` / `pixiv_tag.py`）

| 指令 | 说明 |
| --- | --- |
| `/pid 123456` | 获取单张作品的全部图片 |
| `/随机 3 张 123456 作品` | 随机取该画师 3 张作品（张数 ≤ 20） |
| `/来图 10 张 白丝图` | 按标签搜索 10 张图（数量 ≤ 60） |

`/来图 X 张 XX图` 受 `/设置R18模式` 与 `/设置图片偏好` 影响。

### 美图吧 / 套图（`features/mtb.py`）

| 指令 | 说明 |
| --- | --- |
| `/随机美图吧` | 从已保存套图列表随机抽一套并发送全部图片 |
| `/套图详情 https://www.ku1373.cc/xxx/1.html` | 解析指定套图 |
| `/更新套图列表` | 增量采集最新 2 页（**仅主人可用**） |
| `/全量更新套图列表` | 从第 1 页采到最后一页（**仅主人可用**） |

### 磁力（`features/magnet.py`）

| 指令 | 说明 |
| --- | --- |
| `/磁力猫 关键词` | 搜索磁力（默认 10 条） |
| `/磁力猫 关键词 影视 热度 5` | 带类型 / 排序 / 数量 |
| `/验车 magnet:?xt=urn:btih:...` | 查询磁力详情并发送截图 |

文件类型：`全部` `影视` `音乐` `图像` `文档` `压缩包` `安装包` `其他`
排序方式：`相关度` `文件大小` `添加时间` `热度` `最近下载`

### Cos 图 与 视频（`features/cos_images.py` / `features/sj.py`）

| 指令 | 说明 |
| --- | --- |
| `/2图` | 10 张二次元图 |
| `/3图` | 10 张三次元图 |
| `/骚鸡` / `/烧鸡` / `/sj` | 随机发送一个视频 |

### 订阅与推送（`features/subscribe.py`）

| 指令 | 说明 |
| --- | --- |
| `/订阅画师 123456` | 订阅画师更新 |
| `/取消订阅 123456` | 取消订阅 |
| `/订阅列表` | 查看本会话订阅的画师 |
| `/sp推送` / `/关闭sp推送` | 开启 / 关闭本会话的推送 |
| `/sp状态` | 查看撤回、R18、定时任务等运行状态 |

> 群聊与私聊都支持订阅（原插件只支持群聊）。

### 妹子图图片接口逆向说明

2026-10 对站点前端 JS（`post.js` / `main.js`，javascript-obfuscator 混淆）以及官方
Android APP（美之图 3.8.7，APICloud 混合应用，widget JS 已用 unicorn 模拟 `libsec.so`
解出 RC4 密钥后全部还原）的逆向结论：

#### 1. 网页端接口（kkmzt.com，兜底）

* 文章页 `https://kkmzt.com/photo/{id}`（写真馆）与 `https://kkmzt.com/beauty/{id}`（潮拍馆）
  的 HTML 里只有第一张图，其余图片由 JS 异步拉取；
* 图片列表接口：`GET https://kkmzt.com/app/post/p?id={文章ID}` → `{"data": "<base64>"}`；
* 解密流程：
  1. base64 解码后拆分：`salt = raw[0:16]`、`iv = raw[16:28]`、`密文 + 16 字节 GCM tag = raw[28:]`；
  2. `HKDF-SHA256(ikm = str(文章ID), salt = salt, info = b"")` 派生 32 字节密钥；
  3. `AES-256-GCM` 解密得到图片**文件名**数组；
  4. 完整图片 URL = 文章页首图目录 + 文件名
     （写真馆如 `https://f.meizitu.net/image/2026/04/`，潮拍馆为 `https://f.meizitu.net/mw1024/`）。
* 写真馆（`/photo/`）的网页接口被服务端限制为**前 20 张**（页面上会提示“预览结束，安装美之图APP，免费观赏全片”）；
  潮拍馆（`/beauty/`）网页端没有这个限制，就是全量。

#### 2. APP 端接口（api.iimzt.com，优先，全量）

* 逆向自解密后的 widget JS：`GET https://api.iimzt.com/app/post/p?id={文章ID}`
  （基址来自 `script/utils.js getApiUrl()`，可被 `https://ini.iimzt.com/data/init.json` 的 `api` 字段覆盖）；
* **必须携带 APP 内置的 mTLS 客户端证书**：APK 的 `assets/widget/res/config` 是
  PKCS#12 客户端证书（`CN=AB Client`，密码在解密后的 `res/key.xml` 的 `key` 字段），
  APP 启动时通过 `api.applyCertificates` 安装；**没有证书时 Cloudflare 会对
  `api.iimzt.com` 直接返回 403**（普通请求、浏览器、脚本都一样）。
  仓库已附带该证书 `assets/mzt_client.pem`（证书+私钥，从官方 APK 提取，
  仅用于访问图片接口），克隆后即可使用；若该文件缺失，插件会自动回退网页接口。
* 响应同样是 `{"data": "<payload>"}`，但 payload 是 **hex 字符串**（不是 base64），
  hex 解码后是 **AES-128-CBC + PKCS7** 密文：
  * `key = md5(str(文章ID)).hexdigest()[8:24].lower()`（16 个 ASCII 字符）
  * `iv = 0809060801020609`（来自 APP 解密后的 `res/key.xml`）
* 明文是 JSON **对象**：
  `{"id","date","title","count","thumb","thumbs","images","model","desc",...}`，
  `images` 是**带 `?sign=` 的完整图片 URL**（`https://ap.iimzt.com/...jpg?sign=...`），
  `count` 即全量张数（写真馆也是全量 —— 这就是网页端 20 张限制的根因）；
* 高清图（APP 的 toggleHD）分两种：
  * 写真馆：`https://ap.iimzt.com/...jpg` → `https://ax.iimzt.com/...jpg`
    + `?sign=base64url_nopad(md5("auavax" + path))`（`axkey` 来自 `res/key.xml`）；
  * 潮拍馆（响应带 `thumbs` 列表）：`mw1024` → `mw2000`（新浪图床）。
  插件配置项 `mzt_hd` 控制是否使用高清地址。

#### 3. 插件实现

* `app_core/mzt_crypto.py`：同时支持网页端 GCM（base64）与 APP 端 CBC（hex）两种格式
  （按编码/长度自动识别，失败会换另一种重试）；优先用 `cryptography`，未安装时退回
  纯 Python AES 实现（无需新增依赖）。
* `app_core/mzt.py`：优先 APP 接口（mTLS 全量）→ 网页接口（写真馆 20 张预览）→
  Playwright 页面内 fetch + WebCrypto 兜底；图集信息里会显示数据源与 `已获取/总数`。
> 说明：mTLS 证书来自官方 APP，已随仓库附带 `assets/mzt_client.pem`；
> 若官方后续更换证书，`api.iimzt.com` 会重新 403，此时插件自动回退到网页接口，
> 需要从新版 APK 重新提取证书。
> 提示：若本仓库为公开仓库，该客户端证书也会随之公开；介意的话可将仓库设为私有。

### 设置（`features/recall.py`，**仅主人可用**）

| 指令 | 说明 |
| --- | --- |
| `/开启sp撤回` / `/关闭sp撤回` | 撤回开关 |
| `/设置sp撤回 60` | 设置撤回时间（10 ~ 120 秒） |
| `/设置R18模式 2` | 0 全部 / 1 非R18 / 2 R18 |
| `/设置图片偏好 1` | 0 无偏好 / 1 男性 / 2 女性 |

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
| `mzt_app_api` | `https://api.iimzt.com/app/` | 美之图 APP 图片接口（全量）；留空则只用网页接口 |
| `mzt_hd` | `false` | APP 接口图片是否转高清（写真馆 ax+sign / 潮拍馆 mw2000，流量更大） |
| `mzt_max_images` | `0` | 妹子图单次最多抓取张数，0 表示不限制 |
| `mzt_browser_fallback` | `true` | 妹子图直连接口失败时回退 Playwright |
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
2. **`/设置R18模式` / `/设置图片偏好` / `/设置sp撤回 X`** 做了取值校验，超出范围会提示而不是直接写入。
3. **推送不再依赖 Yunzai 的 `schedule`**：改为插件内部的 asyncio 调度器，
   订阅检查按 `subscribe_check_interval` 小时执行，写真 ID / 套图列表在设定时间每日执行。
4. **合并转发**：原插件需要针对 NapCat 手写 `send_group_forward_msg`，
   AstrBot 版改用标准消息链（`Node` / `Nodes`），由适配器自动选择群/私聊转发接口，
   同时保留 `forward_as_node=false` 时退化为普通消息发送。
5. **数据格式** 由 YAML 统一改为 JSON，便于和 AstrBot 生态一致。
6. **指令触发方式** 从 `filter.regex` 全面切换为 `filter.command`，
   所有指令都以 `/` 开头（与 AstrBot 默认唤醒前缀一致），带参数的指令建议用空格分隔
   （如 `/写真馆 12345`），这样 AstrBot 会直接走命令通道，不会被 LLM 聊天拦截。

---

## 七、常见问题

**Q：发送指令没反应？**
A：① 群里需要 @机器人 或消息以 `/` 开头（私聊直接发即可）；
② 检查 WebUI 里插件是否启用、是否加载失败；
③ 检查是否开启了 `enable_id_whitelist`；
④ 带参数的指令请用**空格分隔**（如 `/写真馆 12345`、`/订阅画师 123456`），
   连写格式（`/写真馆12345`）虽然也兼容，但可能被 LLM 拦截。

**Q：提示"未安装 playwright"？**
A：执行 `playwright install chromium`（或 `python -m playwright install chromium`）。

**Q：图片发不出来 / 提示下载失败？**
A：多为图床风控。可以尝试关闭 `track_pixel`，或调大 `download_timeout`，
或把 `max_concurrent_download` 调小。

**Q：磁力猫搜索失败？**
A：镜像站域名变化很频繁，把 `magnet_cat_sites` 换成当前可用的域名即可。

**Q：为什么 `/写真馆` 只发 20 张图？**
A：分两种情况：
① 正常情况下插件会优先调用官方 APP 接口（`mzt_app_api`，默认 `https://api.iimzt.com/app/`），
   写真馆也能拿到**全量**（图集信息里会显示“数据源: 美之图 APP 接口（全量）”）；
② 如果只显示 20 张且标注“网页接口仅提供前 20 张预览”，说明 APP 接口暂时不可用
   （网络/Cloudflare/接口变更），此时插件已自动退回网页接口，网页端被站点限制为前 20 张。
   可检查 `mzt_app_api` 配置，或稍后重试；潮拍馆（`/潮拍馆`、`/beauty/` 链接）网页端本来就是全量。

**Q：妹子图提示“连接网页失败”？**
A：站点/APP 接口使用 Cloudflare，短时间频繁请求可能被临时限制（HTTP 403）。可以稍等几分钟再试；
插件默认会先直连 APP/网页接口，失败后自动回退 Playwright 浏览器。若浏览器也没安装，按上面安装内核即可。

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

---

## 九、相关项目（友链）

* **TRSS-Yunzai 版涩批插件 sp-plugin**：<https://github.com/1638276310/sp-plugin.git>
  （本插件的 JS 原版，兼容 Miao-Yunzai / TRSS-Yunzai；AstrBot 版的功能与修复会持续同步到该仓库）
