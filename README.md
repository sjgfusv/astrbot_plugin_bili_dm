# bili-dm

B 站私信的 **AstrBot 面板插件**（MIT 协议）。

它不自己登录、不自己轮询——挂在你已经装好的 `bilibili` 私信适配器上，
在 Dashboard 里给你一个聊天界面：会话列表、历史消息、直接回复、表情与视频卡片。

**特性**

- 多会话列表 + 历史消息 + 实时新消息，打开就停在最新一条
- 文本私信、B 站表情、本地图片表情（jpg / gif / png …）
- 视频分享显示成 B 站官方样式卡片，点击打开视频
- 跟随 [AstrBot 调色盘](https://github.com/Sisyphbaous-DT-Project/astrbot_plugin_palette)：透明底、玻璃表面、主色联动
- 自己用面板发的消息不会重复显示；自己不会被当成会话
- 支持独立运行模式（不装 AstrBot 也能预览，带 OneBot 11 接口）

> 前提：AstrBot 里已经启用了一个 B 站私信适配器（例如 `astrbot_plugin_bilibili_adapter`）。
> 面板只是它的「前端」，不改变 AstrBot 收发消息的逻辑。

## 安装

需要 **AstrBot >= 4.29**。

从 GitHub 安装：

```bash
git clone https://github.com/sjgfusv/astrbot_plugin_bili_dm.git
# 把 astrbot_plugin_bili_dm/ 整个目录复制到 AstrBot 的 data/plugins/ 下
# 注意：必须是真实目录，不能用目录联接 / 符号链接（见下方说明）
```

然后在 WebUI「插件」页点一下该插件的「重载」，刷新 Dashboard，
左侧「扩展功能」组里会出现 **B站私信面板 → 私信面板**。

### 在本仓库里开发

源码在本目录的 `astrbot_plugin_bili_dm/`，AstrBot 的 `data/plugins/` 里放的是
**真实拷贝**。改完源码跑一次同步，再到 WebUI 点插件「重载」：

```bash
python sync_to_astrbot.py        # 或双击 sync_to_astrbot.bat
# 自定义 AstrBot 安装位置：
ASTRBOT_DIR=/path/to/astrbot python sync_to_astrbot.py
```

**为什么不能用目录联接**：AstrBot 的插件页面发现会先 `Path.resolve()` 插件目录，
再 `relative_to(plugin_store_path)`；联接会被解析到工作区路径，这一步抛
`ValueError`，结果 `views/` 扫不到——侧栏没有入口，插件详情页的视图也打不开
（报错「未找到该插件可视化视图」）。所以 AstrBot 那边必须是真实目录。
同步脚本会拒绝在联接上执行。

依赖 `httpx` AstrBot 自带，`requirements.txt` 里已经写好。

安装后刷新 WebUI：左侧「扩展功能」组里会出现 **B站私信面板 → 私信面板**；
插件页的卡片上也有视图按钮。改了前端代码后要重载插件才能生效。

插件运行时数据（数据库、配置）在 `AstrBot/data/plugin_data/astrbot_plugin_bili_dm/`，
不在源码目录里，卸载重装不会丢。

## 怎么用

1. 确保「配置 → 消息平台」里的 bilibili 适配器已启用（面板会读它的客户端）
2. 左侧「扩展功能」→ B站私信面板 → 私信面板（或插件卡片上的视图按钮）
3. 左侧点会话开始看，直接在下面输入框回复

面板设置（右上角 ⚙）：

| 设置 | 说明 |
| --- | --- |
| 显示所有会话 | 左侧列出所有给这个账号发过私信的人 |
| 只显示指定的人 | 只显示你填的 UID，界面更干净（不影响 AstrBot 处理其它会话） |
| 自动刷新间隔 | 打开某个会话时，每隔几秒拉一次这个会话的新消息，默认 10s |
| 跟随 AstrBot 调色盘 | 面板底色透明、主色跟随壁纸取色，默认开 |

聊天里还能用 `/bili_panel` 查面板状态。

## 适配 AstrBot 调色盘

装了 [astrbot_plugin_palette](https://github.com/Sisyphbaous-DT-Project/astrbot_plugin_palette)
之后，面板会自动跟随它，不需要额外配置。

为什么需要专门适配：调色盘是把样式**注入 Dashboard 的 `index.html`**，把容器和
`.plugin-view-frame` 都改成透明 + 毛玻璃。但插件视图是独立 iframe 文档，那份 CSS
进不去，面板会变成壁纸上一块死白的板子。

而且这条路走不通——AstrBot 的插件视图 iframe 带
`sandbox="allow-scripts allow-forms allow-downloads"`，**没有 `allow-same-origin`**，
面板既拿不到父页面的 CSS 变量，也读不到 `localStorage`。所以改成后端读配置、前端复刻：

```
data/config/astrbot_plugin_palette_config.json
        │  core/palette.py 解析（主色 / 界面透明度 / 毛玻璃 / 文字增强 / 壁纸）
        ▼
GET /astrbot_plugin_bili_dm/theme  ──bridge──▶  面板按参数写 CSS 变量
```

| 调色盘设置 | 面板里的效果 |
| --- | --- |
| 启用 / 关闭 | 关掉就退回面板自己的配色，不残留玻璃效果 |
| 主色 `theme_primary` | 替换品牌粉：按钮、发出的气泡、选中会话、角标 |
| 界面底色强度 `surface_opacity` | 玻璃表面透明度（0.42/0.60 打底，随它上浮） |
| 界面毛玻璃强度 `stats_card_blur` | 面板内部层的 `backdrop-filter` |
| 文字增强模式 / 强度 | 正文的 `text-shadow`，壁纸上的可读性靠它兜底 |

**一个物理限制**：`backdrop-filter` 不能跨文档采样父页面的壁纸，所以面板里的"玻璃"
是「半透明 + 细边框 + 阴影」模拟的，壁纸在面板背后是清晰的、不会被面板再糊一层。
这也是调色盘自己的插件页的做法。

调色盘改设置 / 换壁纸后，面板每 45 秒跟一次，也可以关掉面板重新打开。

## 表情

输入框左边的 ☺ 打开表情面板，两个分页：

- **B站表情**：从账号同步表情包（`x/emote/user/panel/web`，缓存 6 小时）。点击会插入
  `[笑]` 这类文本转义符，由 B 站客户端自己渲染。没登录、接口没返回或请求失败时，
  用内置常用表情码兜底，不影响发送。
- **本地表情**：导入 jpg / png / gif / webp / bmp（单个 ≤ 2MB），点击缩略图直接以图片私信发出，
  右上角 × 删除。文件存在 `data/plugin_data/astrbot_plugin_bili_dm/stickers/`。

图片表情复用适配器的 `upload_image()` + `send_image_message()`，不新建连接。
插件视图 iframe 没有 `allow-same-origin`，浏览器里用不了 localStorage，
所以本地表情存服务端，换浏览器也能用。

## 它做了什么

```
┌────────────────────── 面板（iframe） ───────────────────────┐
│ 会话列表        │  消息流 / 输入框        │ 运行信息 / 日志   │
└───────┬─────────────────┬──────────────────────────────┬────┘
        │ bridge API      │ SSE                          │
┌───────▼─────────────────▼──────────────────────────────▼────┐
│ astrbot_plugin_bili_dm（本插件）                              │
│  · 读历史：adapter.client.get_messages()                     │
│  · 发消息：adapter.client.send_text_message()                │
│  · 收消息：监听 AstrBot 事件流（platform == bilibili）        │
└───────────────────────┬─────────────────────────────────────┘
                        │ 复用，不新建连接
┌───────────────────────▼─────────────────────────────────────┐
│ 你已启用的 bilibili 私信适配器（登录 / 轮询 / ACK / 图片）      │
└─────────────────────────────────────────────────────────────┘
```

几个刻意的取舍：

- **不建第二条连接**。同一个账号跑两个轮询器会重复收消息、增加风控概率，所以这里全部复用现有 client。
- **时间戳统一成秒**。B 站会话列表的 `session_ts` 是微秒，消息里的 `timestamp` 是秒；
  落库前统一换算，老数据启动时也会修正，避免出现 `NaN-NaN-NaN`。
- **打开就在最新一条**。切换会话/刷新历史后强制即时贴底（绕开 CSS 的平滑滚动），
  图片加载完会再对齐一次；实时收到新消息则改成**平滑滑到底**，不会硬跳一下。
  只有真的新增消息才滚动，定时刷新不会触发动画；你手动往上翻看旧消息时也不会被拽回底部。
- **自己不算会话**。UID 等于登录账号的会话不会出现在列表里，设置里也不能把自己填成对端，发送消息同样拦住。
- **本人头像和昵称**：适配器只提供自己的 UID，面板首次打开时取一次 B 站名片并缓存到 `config.json`（失败 5 分钟内不重试），左栏不再显示「B站账号」占位名。
- **不额外常驻轮询**。历史消息只在「打开会话 / 点刷新」时拉；实时新消息靠 AstrBot 事件流，零额外请求。
- **发出去的消息只显示一条**：发送时先落一条 `local-out-*` 占位记录让界面立刻有反馈；
  历史拉取拿到 B 站真 `msg_key` 后会「认领」同一行而不是再插一条。占位 key 用纳秒时间戳，
  同一秒连发两条也不会撞去重键。启动时还会清理历史遗留的「占位 + 真消息」成对重复行。
- **分享视频做成卡片**：从原始分享数据还原封面、标题和 UP 主，样式接近 B 站官方卡片，
  点击卡片打开视频。封面用 `no-referrer` 绕开防盗链，加载失败时显示渐变占位。
  适配器转成 AstrBot 事件时只保留纯文字，所以实时分享会补拉一次历史拿完整数据。
  原始分享数据里没有时长和播放量，要显示的话得额外调 B 站接口，目前没发这个请求。
- **视频卡片在新标签打开**：AstrBot 默认给插件视图 iframe 的 `sandbox` 没有 `allow-popups`。
  运行 `python patch_astrbot_popups.py` 可加上白名单，脚本会先备份 AstrBot 的原始 JS。
  AstrBot 升级会覆盖这个改动，升级后重跑一次；改完 Ctrl+F5 强刷 Dashboard。
  没打补丁或弹窗被拦时，点击只会**复制链接并提示**，不会把面板跳到那个受限的视频页。
  （`window.open(..., "noopener")` 即使成功也返回 `null`，不能拿它判断弹窗是否被拦。）
- **图片**：历史消息里保留原始 URL，面板直接渲染；适配器把新图片转成了 bytes，实时那条只能显示 `[图片]`，刷新一下就能看到图。
- **自己用面板发的消息**会立刻出现在界面；bot 在别的渠道（比如 AstrBot 对话里）发的回复，下次刷新会话时可见。

## 数据

```
data/plugin_data/astrbot_plugin_bili_dm/
├── config.json   # 显示范围、对端 UID、刷新间隔、本人名片（无 Cookie）
├── messages.db   # 面板自己的消息/会话缓存（可删，删了重新拉）
└── stickers/     # 本地导入的图片表情（index.json + 图片文件）
```

不含 Cookie —— Cookie 始终在 AstrBot 的平台配置里，面板只借用适配器的客户端。

## 已知限制

- 依赖 AstrBot >= 4.29（插件视图 `views/` + `astrbot.api.web`）
- 依赖那个 bilibili 适配器暴露的 `client`（`get_messages` / `send_text_message` / `get_new_sessions`）。
  如果换适配器版本改了方法名，面板会提示「适配器还没就绪」，改 `bridge.py` 里的取用方式即可。
- 图片表情需要适配器提供 `upload_image()` + `send_image_message()`；没有这两个方法的适配器仍可正常发文本。
- 非官方接口，B 站风控与适配器本身相同；面板不增加轮询请求。
- 视频卡片不显示时长和播放量（原始分享数据里没有，需额外调 B 站接口）。
- AstrBot 默认禁止插件 iframe 弹窗，视频卡片想开新标签需运行 `patch_astrbot_popups.py`；详见上文。

## 目录

```
bili-dm/
├── astrbot_plugin_bili_dm/          # ← 插件本体
│   ├── metadata.yaml
│   ├── main.py                      # Star：Web API + 事件监听 + /bili_panel
│   ├── service.py                   # 面板业务：会话/历史/发送
│   ├── bridge.py                    # 桥接现有适配器（找实例、取客户端、解析原始消息）
│   ├── core/                        # store / config / user / emotes / stickers / palette …
│   ├── views/panel/                 # 前端（原生 HTML/CSS/JS）
│   └── .astrbot-plugin/i18n/        # 视图标题
├── local/                           # 独立运行模式（可选，不需要 AstrBot）
├── cli.py, run.py                   # 独立运行入口
├── sync_to_astrbot.py               # 开发用：源码同步到 AstrBot 插件目录
├── patch_astrbot_popups.py          # 可选：给插件 iframe 加 allow-popups
├── preview*.png                     # 界面预览图
└── tests/                           # 冒烟测试（含 AstrBot API 桩）
```

## 开发 / 自测

```bash
python tests/smoke_plugin.py     # 75 项：桥接、会话、发送、调色盘、表情、时间戳兼容、SSE …
python run.py serve --demo       # 独立模式预览界面（假数据）
```

`tests/fake_astrbot/` 是按 AstrBot 4.29.4 源码复刻的最小 API 桩，不联网、不碰真实数据目录。

## 许可

[MIT](LICENSE) — 可自由使用、修改、分发，保留版权声明即可。

## 附：独立运行模式（不使用 AstrBot）

同一份前端也能脱离 AstrBot 跑，自带轮询与 OneBot 11 接口：

```bash
pip install -r requirements.txt
python run.py login          # 扫码登录
python run.py peer 12345678  # 设置对端
python run.py serve          # http://127.0.0.1:8787/
python run.py serve --demo   # 假数据预览
```

CLI：`login` / `peer` / `status` / `send` / `tail` / `serve`。
OneBot：`POST /onebot`、`ws://127.0.0.1:8787/onebot/ws`（`send_private_msg` 等）。
