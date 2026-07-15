# RateGuard

[English](README.md)

RateGuard 是一个本地优先的酒店价格监控工具，面向固定的携程酒店详情页 URL。它通过 Playwright 使用用户自行创建的正常浏览器登录态访问携程网页，提取实际可售的**房型 + 每晚价格**，保存在本地，并在同一酒店、同一房型计划、同一入住日期的价格变动达到阈值时发送飞书通知。

请仅监控自己经营、拥有或已获得授权的酒店。本项目不是携程官方工具；不包含自动下单、自动改价、接口重放或绕过机制。

## 功能

- 监控固定的携程酒店详情页 URL。
- 默认仅采集无早餐价格计划；不保存或展示餐食、取消政策。
- 保存本地 SQLite 历史数据与原始响应文件。
- 与上一轮同酒店、同房型计划、同入住日期的价格进行比较。
- 价格变动达到设定阈值时发送飞书通知；每个完整采集周期还会发送一条“无变动”或异常状态通知。
- Windows 定时任务支持当天每小时监控和远期日期每日两次监控。
- 导出脱敏看板 JSON，以及用于价格铺排的标准化远期房型价格 JSON。

## 架构

```text
Windows 本地电脑（Playwright 定时采集）
  ├─ 本地 SQLite 与原始采集文件
  ├─ 飞书价格变动通知
  └─ 脱敏 dashboard.json ──> Vercel Blob ──> Vercel 网页看板
```

采集器保留在本地，因为它依赖你的浏览器登录态；网页看板可单独部署。

## 环境要求

- Windows 10/11（定时任务脚本基于 Windows Task Scheduler）
- Python 3.10 或以上
- Node.js 20 或以上，以及 `npx`（仅用于把看板数据上传到 Vercel）
- 可以在浏览器中正常登录的携程账号

## 首次部署

```powershell
git clone https://github.com/YOUR_ACCOUNT/RateGuard.git
cd RateGuard

python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
playwright install chromium

Copy-Item configs\config.example.yaml configs\config.yaml
Copy-Item .env.example .env
```

编辑 `configs/config.yaml`：

1. 在 `ctrip_mvp.targets` 中填入本店和竞对酒店的携程详情页 URL。
2. 本店增加 `role: own`，看板会把本店最低可售价格显示为“本店引流价”。
3. 按需要设置 `alert_threshold_yuan` 与无早餐过滤规则。

如需飞书通知，在 `.env` 中填写 Webhook：

```dotenv
RATEGUARD_LARK_WEBHOOK=https://open.feishu.cn/open-apis/bot/v2/hook/...
```

## 登录并首次采集

```powershell
# 打开浏览器，正常完成携程登录后按提示保存登录态。
python -m src.ctrip_mvp --login

# 按指定入住日期采集所有已配置酒店。
python -m src.ctrip_mvp --checkin 2026-07-20
```

登录态保存在 `.secrets/ctrip_state.json`，该文件已被 Git 忽略。携程登录失效时，重新执行登录命令即可。

## 定时任务

首次采集成功后，安装 Windows 定时任务：

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\install_schedule.ps1
```

默认频率如下：

| 时间 | 工作内容 |
| --- | --- |
| 08:00–23:00 | 每小时采集当天入住日期价格 |
| 00:00 | 查询远期锚点：第二天、本周五、下周一、下周五 |
| 12:00 | 再次查询相同的远期锚点 |

每天 12:00 后，前一天入住日期会从“当前价格”视图移除，但本地历史数据和网页看板的“历史记录”仍会保留。电脑需要保持开机、联网，且运行定时任务的 Windows 用户必须处于登录状态。

若没有房型价格变动达到 `alert_threshold_yuan`，飞书会发送一条“无价格变动”采集完成消息；00:00、12:00 的四个远期日期会合并成一条。若采集出现异常，则会发送异常状态，便于排查。

## Vercel 网页看板（可选）

先部署静态网站：

```powershell
cd vercel-dashboard
npm install
npx vercel --prod
```

为 Vercel 项目创建并关联 Blob 存储后，将 `vercel-dashboard/.env.example` 复制为 `vercel-dashboard/.env.local`，填入 Blob 读写 Token。该文件必须只保留在本地。

每次本地采集后，可以手动发布脱敏数据：

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\publish_dashboard.ps1
```

定时任务也会自动调用同一发布脚本。上传已增加 90 秒超时，避免看板同步卡住而影响后续采集。网页看板头部的“下载远期房型价格 JSON”会下载最新的远期价格表（`/api/future-room-prices`）：按入住日期、酒店分组，列出每个已采集的无早房型、每晚价格、可售状态和更新时间。

## 数据与安全

以下文件绝不能提交到公开仓库：

- `.env`、`vercel-dashboard/.env.local`
- `configs/config.yaml`
- `.secrets/`
- `db/`、`logs/`、`output/`

请使用仓库中的 `*.example` 模板。若 Token、Webhook 或登录态意外泄露，请立即撤销或轮换。

网页看板只导出展示所需字段；携程原始响应和浏览器采集文件始终保留在本地。

## 常用命令

```powershell
# 从 SQLite 重新导出本地看板数据
python -m src.dashboard_export

# 手动运行排程逻辑
python -m src.scheduled_run --mode hourly
python -m src.scheduled_run --mode anchors

# 打开本地 Streamlit 看板
streamlit run gui/app.py
```

## 限制

- 携程可能调整网页、登录流程、响应结构或可售规则。
- 采集依赖有效登录态，失效后需要人工重新登录。
- 当前实现只支持固定携程酒店 URL；美团采集不在本项目范围内。
- 使用者应自行遵守相关法律法规和平台服务条款。

## 许可证

[MIT](LICENSE)
