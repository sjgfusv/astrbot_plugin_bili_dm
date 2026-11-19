# bili-dm 插件交接文档

> 整理日期：2026-11-19
> 结构改造完成：2026-11-19（本轮接手已执行第 1、2 项，见下）
> 项目路径：`D:\bili-dm`（由 `D:\QQ机器人\bili-dm` 迁来）
> 仓库：https://github.com/sjgfusv/astrbot_plugin_bili_dm
> 用途：把当前状态与后续待办一次说清，接手人按优先级执行即可。

---

## 一、项目速览

**它是什么**：AstrBot 的 B 站私信面板插件（`astrbot_plugin_bili_dm`）。不自己登录、不自己轮询，复用用户已启用的 `astrbot_plugin_bilibili_adapter` 客户端，在 Dashboard 里提供会话列表、历史消息、直接回复、表情、视频卡片。另有独立运行模式（`tools/local/` + `tools/cli.py` + `tools/run.py`），不装 AstrBot 也能用。

**版本**：0.4.2（实时图片显示）。现在只有两处版本号：`main.py` 的 `@register(...)` 与仓库根目录 `metadata.yaml`，必须一致。

**关键路径**

| 用途 | 路径 |
| --- | --- |
| 源码（本仓库，根目录即插件目录） | `D:\bili-dm\` |
| 插件本体（main/core/views/metadata） | `D:\bili-dm\`（已上移，不再有同名子目录） |
| 独立模式 / 开发工具 / 测试 | `D:\bili-dm\tools\` |
| AstrBot 安装 | `D:\astrbot`，Dashboard `127.0.0.1:6185` |
| 运行中的插件副本 | `D:\astrbot\data\plugins\astrbot_plugin_bili_dm\`（真实拷贝，**不能用 junction**） |
| 运行时数据 | `D:\astrbot\data\plugin_data\astrbot_plugin_bili_dm\`（db / config / stickers） |
| 项目记忆 | `D:\bili-dm\.workbuddy\memory\`（MEMORY.md + 日期日志） |
| 宣传片工程 | `C:\Users\DELL\WorkBuddy\2026-11-18-16-52-02\promo\`（不在本仓库） |

**开发循环**（改任何代码后都要走）：

1. 改 `D:\bili-dm\` 里的源码
2. `python tools/sync_to_astrbot.py`（或双击 `tools/sync_to_astrbot.bat`）同步到 AstrBot 插件目录
3. WebUI 插件页点「重载」，Ctrl+F5 强刷 Dashboard
4. `python tools/tests/smoke_plugin.py`（79 项）；改了前端再跑 `node --check views/panel/app.js`

**发布循环**：本地 git commit → push（Windows 下需 `GIT_SSL_NO_VERIFY=1`）→ 需要发版时打 tag 并用 curl 调 GitHub Releases API（GitHub MCP 无建仓/发版权限）。v0.4.0、v0.4.1 的 Release 已建；本次改动发版为 v0.4.2，实际发布状态以仓库 tag / Release 为准。

---

## 二、待办与完成情况

### 1. 仓库结构：改成「根目录即插件目录」【已完成】

**原风险**：仓库根目录只有 `metadata.yaml` + `logo.png`，真正的 `main.py` / `core/` / `views/` 在子目录里。Cloud 解析能过，但安装走 `_resolve_archive_root_dir()` 会取到仓库根并把归档 flatten 一层，插件可能装成空壳。

**已完成的操作**：

- `main.py` / `service.py` / `bridge.py` / `core/` / `views/` / `.astrbot-plugin/` 整体上移到仓库根目录；原来重复的 `metadata.yaml` / `logo.png` / `README.md` 删除，保留根目录那一份
- `local/` / `cli.py` / `run.py` / `tests/` / `sync_to_astrbot.py(.bat)` / `patch_astrbot_popups.py` / `preview*.png` 移入 `tools/`；新增 `tools/requirements-standalone.txt`（独立模式依赖），根目录 `requirements.txt` 只留插件需要的 httpx
- `tools/_bootstrap.py` 把仓库根注册成 `astrbot_plugin_bili_dm` 包别名——仓库目录名带连字符不能直接 import，这样源码里的相对导入与独立模式/测试的 import 路径都不用改
- `tools/sync_to_astrbot.py` 的 SRC 改为仓库根目录，SKIP 掉 `tools/`、`.workbuddy/`、`data/`、`.git/`、`.venv/` 等；`--clean` 可清目标里的陈旧文件
- `core/config.py` 的默认数据目录、`tools/local/server.py` 的 VIEW_DIR、CLI 帮助里的命令路径全部同步改掉
- README 的安装说明与目录树已重写

**验证结果**：79 项冒烟测试通过；`node --check views/panel/app.js` 通过；`python tools/run.py help` 与独立模式视图路径验证通过；已同步到 `D:\astrbot\data\plugins\astrbot_plugin_bili_dm\`，`main.py` 在根目录，同步报告「3 个更新、22 个未变、删除 0 个」。

**仍需人工确认**：WebUI 插件页点「重载」+ Ctrl+F5，确认侧栏出现「B站私信面板 → 私信面板」且视图能打开；Cloud 上实际走一次安装验证。仓库改动还没 commit / push，Cloud 端暂时拿不到新结构。

### 2. README 的调色盘章节与实际不符【已完成】

README 已按 `views/panel/style.css` 末尾的「完全透明覆盖」规则改写：

- 特性与设置表改为「卡片背景完全透明，壁纸原样透出」
- 调色盘数据链说明去掉「毛玻璃」，明确 `surface_opacity` / `stats_card_blur` 现在只影响 Dashboard 父页面，不再影响面板内部
- 物理限制段落改为「卡片表面全透明，只留细边框；主色按钮和发出气泡仍是实色」
- 旧措辞「玻璃表面 / 半透明底色 / 面板内部 backdrop-filter」已清理

验收：README 与 style.css 覆盖规则一致。

### 3. 确认 AstrBot Cloud 审核状态【待用户本人操作】

**现状**：仓库信息、logo、`metadata.yaml` 此前已补全并推送；v0.4.0 / v0.4.1 Release 已建。新结构与 v0.4.2 需推送后 Cloud 才能拿到，以实际安装结果为准。

**要做的事**：

- 登录 AstrBot Cloud / 插件市场，看提交记录的审核状态
- 若已通过：先把本轮改动 commit + push，再实际走一次安装，验证 `main.py` 在插件根目录、卡片与侧栏视图都能打开
- 若被拒：看驳回理由，通常是 metadata 字段、仓库可见性或描述不合规范
- 若一直没动静：可能需要重新提交或联系平台维护者

**相关文件**：根目录 `metadata.yaml`（`name/display_name/desc/version/author/repo/logo/support_platforms`）、`logo.png`。仓库必须能被平台公开访问。

### 4. 已知功能限制

#### 4.1 视频卡片不显示时长和播放量 —— 已决定不做（2026-11-19）

分享视频卡片只显示封面 / 标题 / UP 主，这是**明确的决定，不是待办**。原始分享数据里没有时长和播放量，要显示得额外调 B 站接口，而插件刻意不增加请求量。若以后有人提出，直接说明「按设计不支持」，不用再评估。

#### 4.2 实时收到的图片显示 —— 已实现（v0.4.2）

适配器会把私信图片下载成 bytes 交给 AstrBot，消息链里没有 URL；但它在 `abm.raw_message` 里保留了原始私信数据。`service.py` 现在从那里取回 `content.url`，只接受 https + B 站图片域名（`.biliimg.com` / `.hdslb.com`），存成 `kind="image"` + URL，前端按历史图片同样的方式渲染；取不到就退回 `[图片]`，`<img>` 加载失败（鉴权/过期）也会回退成 `[图片]`。

**遗留风险**：依赖适配器保留 `raw_message` 这一非文档化字段；URL 可能过期。都不影响收发，最坏退化成占位文字。改适配器让它直接带 URL 是**不建议**的方向——会绕开它刻意做的反 412 鉴权下载，可能触发风控。

---

## 三、其他注意事项

- **不能用目录联接**：AstrBot 插件页面发现会对插件目录做 `resolve()` + `relative_to()`，junction 会抛 ValueError，导致侧栏没入口。插件能 import 成功不代表页面能用。
- **B 站时间戳精度不一致**：`session_ts` 是微秒、`timestamp` 是秒，入库前必须归一，否则前端显示 `NaN-NaN-NaN`。
- **消息去重**：发送先插 `local-out-*` 占位行，历史回吐要认领同一行；占位 key 必须纳秒级。
- **iframe 沙箱**：没有 `allow-same-origin`，拿不到父页 CSS 变量和 localStorage；视频卡片弹窗要靠 `tools/patch_astrbot_popups.py` 加白名单，AstrBot 升级后要重跑。
- **插件 iframe 的 allow-popups 补丁**：AstrBot 升级会覆盖 dist，升级后重跑脚本并 Ctrl+F5。
- **调色盘模式**：卡片表面固定完全透明，只留边框；`surface_opacity` / `stats_card_blur` 不再影响面板。
- **用户偏好**：旁白不要营销号口吻；宣传片要「曼波风格」（用合成声线模拟，非《赛马娘》原声库）。
