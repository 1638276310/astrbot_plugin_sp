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
astrbot_plugin_sp/
├── main.py                       # ⭐ 插件入口：组装 + 全部 29 个指令 handler
├── metadata.yaml                 # AstrBot 插件元数据
├── _conf_schema.json             # 插件配置项（WebUI 可视化编辑）
├── requirements.txt              # 依赖
├── README.md
├── assets/
│   ├── help.png                  # /涩批图片帮助 发送的帮助图（与 build_help_text 同步生成）
│   ├── fonts/
│   │   └── NotoSansSC-Regular.otf  # 帮助图渲染用中文字体
│   ├── jg.json                   # 美图吧套图 URL 出厂种子（64271 条）
│   ├── mztids.json               # 妹子图写真 ID 出厂种子（2479 个）
│   ├── mzt_client.pem            # APP 接口 mTLS 客户端证书（随仓库附带，克隆即可用）
│   ├── sp_video_urls.json        # 骚鸡内置视频直链（首次运行释放到数据目录）
│   └── 说明.txt                  # 帮助图说明
├── tools/
│   ├── mzt_diag.py               # 妹子图 APP 接口（mTLS + 解密）诊断脚本（独立运行）
│   ├── selftest_mzt_chain.py     # 妹子图新链路自检（离线 fixture，10 组断言）
│   └── fixtures/
│       └── app_post_172250.json  # APP 接口密文 fixture（仅用于离线解密测试）
├── app_core/                     # 公共能力（一个文件一件事）
│   ├── __init__.py
│   ├── paths.py                  # 数据/临时/资源目录
│   ├── settings.py               # 配置读取 + 上游接口与常量
│   ├── storage.py                # JSON 持久化
│   ├── http.py                   # aiohttp 异步请求封装
│   ├── browser.py                # Playwright 无头浏览器封装（美图吧等使用）
│   ├── imaging.py                # 噪点处理 / base64 / 扩展名识别
│   ├── render.py                 # 帮助图渲染 + 中文字体诊断（Pillow）
│   ├── filecache.py              # 图片下载缓存
│   ├── message.py                # 消息发送、合并转发、临时文件、权限
│   ├── scheduler.py              # 内置定时任务调度器
│   ├── pixiv.py                  # P 站接口客户端（含订阅查询）
│   ├── verify.py                 # 验车（磁力详情）
│   ├── magnetcat.py              # 磁力猫搜索
│   ├── mzt.py                    # 妹子图链路：指令 ID → APP 接口(mTLS) → 内置解密
│   ├── mzt_crypto.py             # 妹子图 APP 接口解密（AES-128-CBC/PKCS7）
│   └── mtb.py                    # 美图吧（ku1373）解析
└── features/                     # 🎯 功能模块：一个功能一个文件
    ├── __init__.py
    ├── _base.py                  # 各功能模块的基类
    ├── _pixiv_base.py            # P 站功能公共逻辑
    ├── help.py                   # 帮助（文字版内容 + 图片版渲染）
    ├── recall.py                 # 撤回 / R18 / 图片偏好 / 状态的解析正则
    ├── urls.py                   # 网址导航分组
    ├── sj.py                     # 随机短视频
    ├── pixiv_pid.py              # /pid
    ├── pixiv_artist.py           # /随机 X 张 Y 作品
    ├── pixiv_tag.py              # /来图 X 张 XX图
    ├── mzt.py                    # 妹子图
    ├── mtb.py                    # 美图吧套图
    ├── magnet.py                 # 磁力猫 / 验车
    ├── cos_images.py             # 2图 / 3图
    └── subscribe.py              # 画师订阅与推送
```

**新增一个功能怎么做？**
在 `features/` 下新建 `.py`，写一个继承 `spFeature` 的类放业务方法；
**指令 handler 必须写在 `main.py` 的 `spPlugin` 主类上**（AstrBot 只扫描插件类，
所以 handler 不能只写在 Mixin 里），用 `@filter.command("命令名", priority=N)` 注册，
再把新类加进 `features/__init__.py` 的导入与 `main.py` 的继承列表即可。

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
pip install -r requirements.txt
```

> `requirements.txt` 只包含运行必需的 4 个依赖（aiohttp / numpy / playwright / Pillow）。
> 可选加速：安装 `cryptography` 后妹子图解密会走 C 实现；不装则自动使用内置纯 Python
> AES 实现，功能不受影响（因此不写进 requirements，避免强制新增依赖）。

### 3. 安装浏览器内核（**磁力猫 / 验车 / 美图吧 必须**）

这几个功能需要抓取网页，依赖 Playwright：

```bash
playwright install chromium
```

> 妹子图（`/写真馆`、`/潮拍馆`）只走 APP 接口（mTLS）+ 内置解密，不启动浏览器；
> 如果不需要磁力猫 / 验车 / 美图吧，可以不装浏览器内核。
> 用到需要浏览器的指令时，如果缺少内核，插件会返回明确的提示而不会崩溃。

### 4. 重载插件

打开 AstrBot WebUI → 插件管理 → 找到「涩批」→ 点击重载。

### 5. 帮助图

`/涩批图片帮助` 优先直接发送插件自带的 `assets/help.png`：

- 760px 宽、卡片式长图，内容由 `features/help.py` 的 `build_help_text()` 生成；
- 与文字帮助使用同一份指令清单；修改帮助文本后需重新生成该图（或删除 `help.png`，
  让插件在下次调用时用 Pillow 现场渲染）；
- 想换图时保持文件名 `help.png` 覆盖即可，不需要改代码。

如果 `help.png` 缺失，插件会自动用 Pillow 渲染兜底；再失败才发送文字版。

---

## 三、指令一览

> 本插件共有 **29 个主指令 + 32 个别名**（合计 61 个触发词），全部用
> `filter.command` 注册在 `main.py` 的 `spPlugin` 类上；下表的指令名、别名与参数
> 均与代码注册逐项一致。
>
> AstrBot 中唤醒前缀默认是 `/`；群聊 @机器人 或消息以 `/` 开头都能触发，私聊直接发也能触发。
> AstrBot 会按 handler 参数类型自动解析命令后的参数：**命令名与参数之间用空格分隔**
> （如 `/写真馆 12345`、`/设置sp撤回 60`），带参数的指令请使用空格形式。
> 兼容性说明：`/pid123456`、`/订阅画师123456`、`/磁力猫关键词` 这类连写也会被内置正则兜底识别，
> 但可能被 LLM 聊天拦截，**推荐一律用空格**。

### 帮助（2 个主指令）

| 主指令 | 别名 | 参数 | 说明 |
| --- | --- | --- | --- |
| `/涩批文字帮助` | `/sp文字帮助` `/色批文字帮助` `/色胚文字帮助` `/涩胚文字帮助` | 无 | 发送文字版帮助（消息里带“图片”时改发图片版） |
| `/涩批图片帮助` | `/sp图片帮助` `/色批图片帮助` `/色胚图片帮助` `/涩胚图片帮助` | 无 | 发送 `assets/help.png` 帮助长图 |

### 网址导航（`features/urls.py`，1 个主指令 + 7 个别名）

| 主指令 | 别名 | 参数 | 说明 |
| --- | --- | --- | --- |
| `/写真网址` | `/福利网址` `/吃瓜网址` `/导航网址` `/福利App` `/福利APP` `/福利app` `/TG电报` | 无 | 按触发词返回对应分组（写真/福利/吃瓜/导航/福利App/TG电报）的网址列表；内容在 WebUI 的 `url_groups` 中维护 |

> 在 `url_groups` 里新增名为 `XX` 的分组后，可用 `/XX网址` 获取（由代码动态匹配）。

### 妹子图（`features/mzt.py`，4 个主指令）

| 主指令 | 别名 | 参数 | 说明 |
| --- | --- | --- | --- |
| `/写真馆` | 无 | `<ID或链接>`，可多个 | 按 ID/链接获取写真，如 `/写真馆 171317`、`/写真馆 171317 171318`；一次最多 10 个 |
| `/潮拍馆` | 无 | `<ID或链接>`，可多个 | 获取潮拍馆图集，用法同上，一次最多 10 个 |
| `/随机写真` | 无 | 无 | 从数据目录 `mztids.json` 里随机取一个 ID（内置 2479 个种子） |
| `/更新写真ID` | `/更新写真id` | 无 | 抓取站点最新 ID，并与内置种子、现有数据增量合并（不删除旧 ID） |

> 妹子图只走一条链路：**指令里的 ID → 拼 APP 接口地址 → 包内 mTLS 证书请求 →
> 内置解密模块解出完整图片 URL → 逐张下载**。
> 不访问站点 HTML 页面、不请求网页接口、也不启动浏览器，因此没有 Cloudflare
> 挑战页等待/误判，也没有直连兜底。
> APP 接口基址、高清开关、mTLS 证书路径均已内置，**无需在 WebUI 配置**；
> 出站请求默认使用插件所在设备的本机 IP 直连；若服务器出口被 Cloudflare
> 风控，可显式设置 `SP_MZT_PROXY` 走干净代理/隧道（见下方 403 排查）。
> 详细逆向说明见下方 [「妹子图图片接口逆向说明」](#妹子图图片接口逆向说明)。

### P 站（`features/pixiv_pid.py` / `pixiv_artist.py` / `pixiv_tag.py`，3 个主指令）

| 主指令 | 别名 | 参数 | 说明 |
| --- | --- | --- | --- |
| `/pid` | 无 | `<数字>` | 获取该作品的全部图片，如 `/pid 123456` |
| `/随机` | 无 | `<张数> 张 <画师ID> 作品` | 随机取该画师的作品，张数 1-20，如 `/随机 3 张 123456 作品` |
| `/来图` | 无 | `<张数> 张 <标签>图` | 按标签搜索，张数 1-60，如 `/来图 10 张 白丝图` |

`/来图` 的结果受 `/设置R18模式` 与 `/设置图片偏好` 影响。

### 美图吧 / 套图（`features/mtb.py`，4 个主指令）

| 主指令 | 别名 | 参数 | 说明 |
| --- | --- | --- | --- |
| `/随机美图吧` | 无 | 无 | 从已保存套图列表随机抽一套并发送全部图片 |
| `/套图` | `/套图详情` | `<URL>` | 解析指定套图，如 `/套图 https://www.ku1373.cc/xxx/1.html`（旧名仍兼容） |
| `/更新套图列表` | 无 | 无 | 增量采集最新 2 页（**仅主人可用**） |
| `/全量更新套图列表` | 无 | 无 | 从第 1 页采到最后一页（**仅主人可用**） |

### 磁力（`features/magnet.py`，2 个主指令）

| 主指令 | 别名 | 参数 | 说明 |
| --- | --- | --- | --- |
| `/磁力猫` | 无 | `<关键词> [文件类型] [排序] [数量]` | 搜索磁力；数量 1-50、默认 10，如 `/磁力猫 关键词 影视 热度 5` |
| `/验车` | 无 | `<magnet:...>` | 查询磁力详情并发送截图（最多 9 张），如 `/验车 magnet:?xt=urn:btih:...` |

文件类型：`全部` `影视` `音乐` `图像` `文档` `压缩包` `安装包` `其他`
排序方式：`相关度` `文件大小` `添加时间` `热度` `最近下载`

> 关键词本身不能包含空格（空格会被解析为下一个参数）；中文关键词直接写即可。

### Cos 图 与 视频（`features/cos_images.py` / `features/sj.py`，3 个主指令）

| 主指令 | 别名 | 参数 | 说明 |
| --- | --- | --- | --- |
| `/2图` | 无 | 无 | 10 张二次元图 |
| `/3图` | 无 | 无 | 10 张三次元图 |
| `/骚鸡` | `/烧鸡` `/sj` | 无 | 随机发送一个短视频 |

### 订阅与推送（`features/subscribe.py`，5 个主指令）

| 主指令 | 别名 | 参数 | 说明 |
| --- | --- | --- | --- |
| `/订阅画师` | 无 | `<画师ID>` | 订阅画师更新，如 `/订阅画师 123456` |
| `/取消订阅` | 无 | `<画师ID>` | 取消订阅，如 `/取消订阅 123456` |
| `/订阅列表` | 无 | 无 | 查看本会话订阅的画师 |
| `/sp推送` | 无 | 无 | 开启本会话的推送 |
| `/关闭sp推送` | 无 | 无 | 关闭本会话的推送 |

> 群聊与私聊都支持订阅（原插件只支持群聊）。

### 妹子图图片接口逆向说明

2026-10 对官方 Android APP（美之图 3.8.7，APICloud 混合应用，widget JS 已用 unicorn
模拟 `libsec.so` 解出 RC4 密钥后全部还原）的逆向结论。插件**只使用 APP 接口**：

#### 1. 接口与证书

* `GET https://api.iimzt.com/app/post/p?id={文章ID}`
  （基址来自 `script/utils.js getApiUrl()`，可被 `https://ini.iimzt.com/data/init.json`
  的 `api` 字段覆盖）；
* **必须携带 APP 内置的 mTLS 客户端证书**：APK 的 `assets/widget/res/config` 是
  PKCS#12 客户端证书（`CN=AB Client`，密码在解密后的 `res/key.xml` 的 `key` 字段），
  APP 启动时通过 `api.applyCertificates` 安装；**没有证书时 Cloudflare 会对
  `api.iimzt.com` 直接返回 403**（普通请求、浏览器、脚本都一样）。
  仓库已附带该证书 `assets/mzt_client.pem`（证书 + 私钥，从官方 APK 提取，
  仅用于访问图片接口）。
* 证书路径已内置为 `assets/mzt_client.pem`（随源码附带），**无需配置**；
  旧路径 `app_core/mzt_client.pem` 兼容。仅排障时可用环境变量
  `SP_MZT_APP_CERT` 覆盖（只接受插件目录内相对路径）。
* 响应同样是 `{"data": "<payload>"}`，但 payload 是 **hex 字符串**（不是 base64），
  hex 解码后是 **AES-128-CBC + PKCS7** 密文：
  * `key = md5(str(文章ID)).hexdigest()[8:24].lower()`（16 个 ASCII 字符）
  * `iv = 0809060801020609`（来自 APP 解密后的 `res/key.xml`）
* 明文是 JSON **对象**：
  `{"id","date","title","count","thumb","thumbs","images","model","desc",...}`，
  `images` 是**带 `?sign=` 的完整图片 URL**（`https://ap.iimzt.com/...jpg?sign=...`），
  `count` 即全量张数（写真馆也是全量）。
* 高清图（APP 的 toggleHD）分两种：
  * 写真馆：`https://ap.iimzt.com/...jpg` → `https://ax.iimzt.com/...jpg`
    + `?sign=base64url_nopad(md5("auavax" + path))`（`axkey` 来自 `res/key.xml`）；
  * 潮拍馆（响应带 `thumbs` 列表）：`mw1024` → `mw2000`（新浪图床）。
  插件内置常量 `MZT_HD_DEFAULT=false` 控制是否使用高清地址；默认关闭
  （全量普通图已够用，高清流量更大）。如需临时开启，设置环境变量
  `SP_MZT_HD=1` 即可，不需要改 WebUI。

#### 3. 插件实现

* `app_core/mzt_crypto.py`：实现 APP 端 AES-128-CBC/PKCS7 解密；优先用
  `cryptography`，未安装时退回纯 Python AES 实现（无需新增依赖）。
* `app_core/mzt.py`：`parse_article_refs()` 从指令取 ID（支持多个）→
  `_app_api_url()` 用内置基址拼 `/post/p?id={id}` → `_app_ssl_context()` 加载随包
  证书 → `fetch_response()` 请求 → `decode_api_payload()` 解密 → `AlbumInfo`；
  失败统一抛 `MztFetchError`，**不再回退网页接口 / 浏览器 / 首图**。
* `features/mzt.py`：只按 `album.image_urls`（解密返回的完整图片 URL）逐张下载，
  保留原有命名、并发与失败重试策略。

> 说明：mTLS 证书来自官方 APP，已随仓库附带 `assets/mzt_client.pem`；
> 若官方后续更换证书，`api.iimzt.com` 会重新 403，需要从新版 APK 重新提取证书
> （插件不会回退网页接口）。
> 提示：若本仓库为公开仓库，该客户端证书也会随之公开；介意的话可将仓库设为私有。

### 403 / Cloudflare 排查与直连（`app_core/mzt.py`）

> 实测结论（2026-10）：插件只请求 `api.iimzt.com`（mTLS）。证书有效时接口返回全量图片；
> 证书缺失/无效时 Cloudflare 直接 403。插件不再访问站点 HTML 页面，因此没有挑战页
> 等待，也不存在“挑战页被当成成功”的问题。

#### 1. 链路与日志

`/写真馆`、`/潮拍馆` 只有一条链路：指令 ID → APP 接口（mTLS）→ 内置解密 → 按解密结果下载。
每个阶段输出一行 info 日志（URL 已去掉 `sign` 等 query 值）：

```
[mzt] stage=cert path=mzt_client.pem state=ok
[mzt] stage=app url=https://api.iimzt.com/app/post/p?id=171317 status=200 cf-ray=... elapsed=380ms
[mzt] stage=decrypt id=171317 state=ok scheme=app count=69 images=69 status=200 cf-ray=...
[mzt] chain=app+decrypt id=171317 kind=photo images=69 total=69
[mzt] result source=app images=69 total=69 truncated=false
```

失败时用户看到（`format_fetch_error` 脱敏、定长）：

```
获取图集失败，请稍后再试（APP接口:Cloudflare 拦截 HTTP 403（Ray ID ...）；文章页同样被 Cloudflare 拦截（出口 IP/区域策略，非证书/配置问题）；请更换设备网络出口，或设置 SP_MZT_PROXY 走干净代理后重试）
```

解密成功但图片列表为空或数量异常时，日志会额外记录 kind/状态码/ray，例如：

```
[mzt] stage=decrypt id=171317 state=empty kind=app count=0 status=200 cf-ray=...
[mzt] stage=decrypt id=171317 state=count-mismatch count=69 images=20 status=200 cf-ray=...
```

> 旧版的网页接口 / 浏览器 / 首图兜底已移除；`/更新写真ID` 已恢复为
> “站点列表页 + 内置种子 + 现有数据”的增量合并（只增不删）。
> 因此不会再有挑战页轮询，也不会把“没有新ID”当成成功。

#### 2. 内置默认值（无需用户配置）

2026-10 起以下 4 项已从 WebUI 配置移除，全部由代码内置，用户不需要填写：

| 内置常量 | 默认值 | 说明 |
| --- | --- | --- |
| `MZT_APP_API_DEFAULT` | `https://api.iimzt.com/app/` | 美之图 APP 接口基址（全量图片） |
| `MZT_HD_DEFAULT` | `false` | 是否转高清（写真馆 ax+sign / 潮拍馆 mw2000）；默认关闭 |
| `MZT_APP_CERT_DEFAULT` | `assets/mzt_client.pem` | 随包 mTLS 证书（PEM 同时含 CERTIFICATE 与 PRIVATE KEY） |

> **默认直连 + 显式代理**：默认使用插件所在设备的本机 IP 和系统默认路由，
> **不做自动代理回退**。aiohttp 会话显式 `trust_env=False`，所以系统设置的
> `HTTP(S)_PROXY` / `ALL_PROXY` 不会生效；只有显式设置 `SP_MZT_PROXY`
> （HTTP(S) CONNECT 代理）时，APP 接口、出口探针和图片下载才会走该代理。
> 请求走 TLS，服务端证书按系统 CA 链严格校验（`CERT_REQUIRED` + 主机名校验），
> 同时加载随包客户端证书完成 mTLS。
> 排障用的可选覆盖入口（**不是必填项**）：环境变量 `SP_MZT_APP_API`、
> `SP_MZT_HD`、`SP_MZT_APP_CERT`、`SP_MZT_PROXY`（兼容 `MZT_*` 旧名）。
> 美图吧浏览器可用 `SP_MTB_PROXY`；未单独设置时复用 `SP_MZT_PROXY`。
> 旧 WebUI 的 `mzt_app_api` / `mzt_hd` / `mzt_proxy` / `mzt_app_cert` 已忽略，
> 启动时会记录 `[mzt] config=legacy state=ignored` 日志。

> 证书缺失/无效时 APP 接口必然被 Cloudflare 403（这**不是** IP 问题）；
> 确认 `assets/mzt_client.pem` 已随插件一起上传。

#### 2.1 服务器出口被 Cloudflare 风控时（可选）

如果 `tools/mzt_diag.py` 显示 `cert_ok=true`、`egress` 正常，但 `app_api.status=403`
且正文是 `Sorry, you have been blocked`，说明是**出口 IP 被 Cloudflare 风控**，
不是证书或代码问题。此时换一个干净出口，并显式告诉插件：

```bash
# 示例：代理监听在本机 127.0.0.1:7897（Clash/Mihomo/SSH 反向隧道均可）
export SP_MZT_PROXY=http://127.0.0.1:7897
# 可选：美图吧浏览器也走同一代理；不设置时自动复用 SP_MZT_PROXY
export SP_MTB_PROXY=http://127.0.0.1:7897

# 先验证代理出口是否真的能过 Cloudflare
python tools/mzt_diag.py --id 171317 --proxy "$SP_MZT_PROXY"

# 再重启 AstrBot，让插件重新读取环境变量
```

也可以直接用 SSH 反向隧道把本地代理借给服务器（仅用于临时验证）：

```bash
# 本地执行：把服务器的 7897 转发到本地 Clash 的 7897
ssh -R 7897:127.0.0.1:7897 ubuntu@<服务器IP>
# 服务器执行
export SP_MZT_PROXY=http://127.0.0.1:7897
python tools/mzt_diag.py --id 171317
```

> 代理地址只从环境变量读取，不写入 WebUI；日志只打印 `route=proxy`，
> 不会输出代理 URL/账号密码。建议用本机 loopback 隧道，避免把代理账号写进配置。

#### 3. 独立诊断脚本

不依赖 AstrBot 启动，直接看出口 IP / DNS / 时间 / 包内证书 / APP 接口状态与解密张数 / 图片 CDN：

```bash
# 在插件根目录（astrbot_plugin_sp/）执行
python tools/mzt_diag.py --id 171317
python tools/mzt_diag.py --id 171317 --json > mzt_diag.json
# 验证显式代理出口
python tools/mzt_diag.py --id 171317 --proxy http://127.0.0.1:7897
```

输出已脱敏（无 Cookie / 证书内容 / `sign=` 值 / 代理账号），其中：

* `target.proxy`：`direct` 或 `proxy`（不输出代理地址）；
* `egress.ip / loc / colo`：实际出口 IP 与 Cloudflare 处理节点；
* `certificates`：包内相对路径候选、PEM 是否完整、能否 `load_cert_chain`；
* `app_api`：状态码、`cf_ray`、解密张数（`decode.images`）与首图 CDN 探测；
* `summary.likely_cause`：一句话判断（例如“APP 接口被 Cloudflare 拦截：确认 mTLS 证书有效”）。

#### 4. 常见处理

* **APP 403 + `cf-ray`**：① 证书没加载/被官方更换 → 检查 `certificates` / `stage=cert`；
  ② 出口被风控 → 换设备网络出口，或设置 `SP_MZT_PROXY` 走干净代理后重跑诊断；
  插件默认直连，但不会自动换代理（避免隐藏出口）；
* **`stage=decrypt ... state=empty`**：解密成功但图片列表为空/数量异常，日志会带 `status/cf-ray`，
  插件直接报错，不会改走其它链路；
* **同一篇连续失败**：进程内 3 秒限速（日志 `[mzt] rate-limit wait=...ms`），不要连续狂刷。

> APP 接口请求统一使用官方 APP UA、`Accept-Language: zh-CN` 与站点 Referer；
> 请求走 `app_core/http.py` 的 `fetch_response()`（含 800~1500ms 抖动重试），
> 妹子图不再启动浏览器。

### 设置（`features/recall.py`，**仅主人可用**，5 个主指令）

| 主指令 | 别名 | 参数 | 说明 |
| --- | --- | --- | --- |
| `/开启sp撤回` | `/关闭sp撤回` `/开启涩批撤回` `/关闭涩批撤回` `/开启色批撤回` `/关闭色批撤回` `/开启色皮撤回` `/关闭色皮撤回` `/开启色胚撤回` `/关闭色胚撤回` | 无 | 撤回开关：同一个指令，消息以“开启/关闭”开头即切换为对应状态 |
| `/设置sp撤回` | `/设置涩批撤回` `/设置色批撤回` `/设置色皮撤回` | `<10-120>` | 设置撤回时间（秒），如 `/设置sp撤回 60` |
| `/设置R18模式` | `/设置r18模式` | `<0\|1\|2>` | 0 全部 / 1 非R18 / 2 R18，如 `/设置R18模式 2` |
| `/设置图片偏好` | 无 | `<0\|1\|2>` | 0 无偏好 / 1 男性 / 2 女性，如 `/设置图片偏好 1` |
| `/sp状态` | 无 | 无 | 查看撤回、R18、图片偏好、合并转发、定时任务等运行状态 |

---

## 四、配置项（WebUI 可视化编辑）

打开 AstrBot WebUI → 插件管理 → 涩批 → 配置。常用项：

| 配置项 | 默认值 | 说明 |
| --- | --- | --- |
| `pixiv_api_base` | `https://pid.kkndp.cn` | P 站接口地址，失效时在此更换 |
| `dingyue_api` / `dingyue_key` | `https://user.kkndp.cn` / 空 | 订阅推送接口与 key |
| `magnet_api` | `https://whatslink.info/api/v1/link` | 验车接口 |
| `magnet_cat_sites` | `https://魔法猫咪.lol` + 4 个历史镜像 | 磁力猫镜像，按顺序尝试；失效自动回退 |
| `mzt_site` | `https://kkmzt.com` | 妹子图站点（APP 请求 Referer 与展示地址） |
| `mzt_max_images` | `0` | 妹子图单次最多抓取张数，0 表示不限制 |
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
| `random_interval` | `2` | 同一次请求内多张图片/作品之间的随机等待上限（秒） |
| `enable_scheduler` | `true` | 是否启用内置定时任务 |
| `subscribe_check_interval` | `4` | 订阅推送检查间隔（小时） |
| `scheduler_mtb_cron` | `09:30` | 套图列表自动更新时间 |
| `enable_id_whitelist` / `id_whitelist` | `false` / `[]` | 会话白名单 |
| `enable_group_limit` | `true` | 是否限制订阅数量 |
| `max_subscribe_sessions` / `max_artists_per_session` | `5` / `20` | 订阅上限 |
| `forward_as_node` | `true` | 多图是否用合并转发发送 |

> 妹子图的 APP 接口基址 / 高清开关 / mTLS 证书路径已从 WebUI 移除并内置
> 到代码（见上方 [「内置默认值」](#2-内置默认值无需用户配置)），无需填写；
> 出站默认直连，不做自动代理回退；需要代理时用 `SP_MZT_PROXY`
> （美图吧用 `SP_MTB_PROXY`）显式指定，排障时也可用其它 `SP_MZT_*` 覆盖。

**如果某些指令一直提示"未安装 playwright"**：执行 `playwright install chromium` 即可；
**如果磁力猫搜不到东西**：多半是镜像站换了域名，把 `magnet_cat_sites` 换成新域名。

---

## 五、数据存放位置

所有数据都在 `AstrBot/data/plugin_data/astrbot_plugin_sp/`：

| 文件 | 内容 |
| --- | --- |
| `sp_video_urls.json` | 骚鸡视频直链列表（首次运行自动从 assets 释放 3.9w+ 内置网址，避免卡死配置页面且更新不覆盖） |
| `dingyue.json` | 画师订阅数据 |
| `mztids.json` | 妹子图写真 ID 列表（首次运行从 assets 种子初始化，已有数据只增量合并） |
| `jg.json` | 美图吧套图 URL 列表（首次运行从 assets 种子初始化，已有数据只增量合并） |
| `runtime.json` | 定时任务上次执行时间 |
| `cache/` | 图片下载缓存（可随时删除） |
| `temp/` | 临时图片/视频（插件启动会自动清理 1 小时前的文件） |

> 种子数据初始化规则：目标文件缺失/为空时从 `assets/` 复制；已有数据时
> 只做“现有 + 种子”的去重增量合并，绝不删除或全量覆盖。所有写入使用
> 临时文件 + 原子替换；长时间采集每 300 页定期落盘一次。

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
2. **`/设置R18模式` / `/设置图片偏好` / `/设置sp撤回 60`** 做了取值校验，超出范围会提示而不是直接写入。
3. **推送不再依赖 Yunzai 的 `schedule`**：改为插件内部的 asyncio 调度器，
   订阅检查按 `subscribe_check_interval` 小时执行，套图列表在设定时间每日执行。
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

**Q：`/写真馆` 提示获取失败 / 只拿到部分图片？**
A：妹子图只走 APP 接口（mTLS + 内置解密），失败时**不会**回退网页。常见原因：
① `assets/mzt_client.pem` 缺失/失效，日志会记 `stage=cert state=cert-invalid:*`；
② 出口 IP 被 Cloudflare 风控（提示 `APP接口:Cloudflare 拦截 HTTP 403`），
   插件默认使用设备本机 IP 直连、不会自动代理；可更换设备网络出口，
   或设置 `SP_MZT_PROXY` 走干净代理后重试；
③ 配置了 `mzt_max_images` > 0 时会按配置截断，消息头会标注“已按配置 mzt_max_images 截断”。
排查：在插件根目录执行 `python tools/mzt_diag.py --id 171317`，看 `summary.likely_cause`。

**Q：妹子图提示“获取图集失败”？**
A：错误文案会带阶段、状态码与 Ray ID，例如
`获取图集失败，请稍后再试（APP接口:Cloudflare 拦截 HTTP 403（Ray ID ...）；...）`。
处理顺序：① 跑 `python tools/mzt_diag.py --id 171317` 看 `certificates` 与 `app_api`；
② APP 接口 403 多为证书未加载/被更换，检查 `assets/mzt_client.pem`；
③ 证书正常仍 403 时说明出口被 CF 拦截：插件默认直连、不会自动代理，
   请更换设备网络出口，或设置 `SP_MZT_PROXY` 走干净代理后重试；
④ 详见上方 [「403 / Cloudflare 排查与直连」](#403--cloudflare-排查与直连app_coremztpy)。

**Q：P 站接口报错 / 返回格式异常？**
A：`pixiv_api_base` 使用的是第三方接口，接口失效时更换为可用接口。

**Q：订阅推送不生效？**
A：① `dingyue_api` 与 `dingyue_key` 必须正确；
② 需要先 `/订阅画师 <画师ID>`，再 `/sp推送` 开启；
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
