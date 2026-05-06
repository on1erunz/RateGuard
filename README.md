# RateGuard — 酒店 OTA 价格守护助手

> **单脚本，零成本，不睡不累的价格哨兵。**

RateGuard 是一款开源的酒店 OTA 价格监控工具。输入城市/坐标，自动搜索附近酒店、抓取价格、比对规则、异常告警。适用于中小酒店运营者、收益经理和任何需要盯价格的人。

---

## 快速开始

### 前置条件

- **Python ≥ 3.10**
- **Chrome / Chromium**（Playwright 自动下载）

### 5 分钟跑起来

```bash
# 1. 克隆
git clone https://github.com/Docking666/RateGuard.git
cd RateGuard

# 2. 安装依赖（自动下载浏览器）
pip install -r requirements.txt
playwright install chromium

# 3. 复制配置模板
cp configs/config.example.yaml configs/config.yaml
vim configs/config.yaml   # 填入你的配置

# 4. 跑一次
python -m src.main
```

搞定。prices 表里已经有数据。

---

## 工作流

```
config.yaml  →  输入监控目标（城市/坐标/底价/规则）
      ↓
main.py     →  Playwright 搜索 OTA → 抓取酒店价格
      ↓
database.py →  存入 SQLite（price_log + hotel、room_type 基准表）
      ↓
rules.py    →  规则引擎判定异常 → 触发告警
      ↓
notify.py   →  飞书机器人通知（推荐）/ 邮件
      ↓
report.py   →  生成静态 dashboard HTML（可选）
```

---

## 配置说明

`configs/config.yaml` 核心字段：

```yaml
search:
  mode: city        # city | coords
  city: "深圳市"    # city 模式：城市名
  coords:            # coords 模式：中心坐标 + 半径
    lat: 22.5362
    lng: 113.9514
    radius_km: 5

hotel:
  default_base_price: 300   # 默认底价（所有房型兜底）
  checkin_date: "2026-05-20"
  checkout_date: "2026-05-25"

competitors:
  target_platforms:          # 爬取哪些 OTA，按优先级排列
    - ctrip
    - meituan
    - fliggy
  max_hotels: 20             # 每次最多抓取多少家酒店

rules:
  - type: "undercut_check"   # 始终确认与竞对的最低价差距
    max_undercut_pct: 30     # 允许低于竞对最多 30%（百分比）
    min_price_abs: 300       # 绝对底价：不低于 300 元
  - type: "gap_alert"
    gap_threshold: 20        # 竞对价格偏差超过 20 元时告警

notifications:
  lark:
    enabled: false           # 暂未配置 true
    webhook: "https://open.larksuite.com/open-apis/bot/v2/hook/xxx"
  email:
    enabled: false
    smtp_host: "smtp.gmail.com"
    smtp_port: 587
    from_addr: "your@gmail.com"
    smtp_pass: "your-app-password"
    to_addr: "manager@example.com"
```

说明：v1.0 不配置通知也能正常爬取数据。先通数据流，再补告警。

---

## 数据流

| 阶段 | 说明 | 预期输出 |
|---|---|---|
| ① 爬取 | Playwright 搜索 OTA 酒店列表 → 逐页提取名称/价格/房型 | 原始价格日志 |
| ② 清洗 | 去重 + 缺失值填充 + 日期标准化 | 结构化的 price_log |
| ③ 检测 | 规则引擎检查：底价保护、竞对差距、异常跳变 | 告警事件 |
| ④ 通知 | 飞书机器人 / 邮件推送告警 | 飞书消息或邮件 |
| ⑤ 看板 | 生成单文件 dashboard.html | 浏览器打开即看 |

---

## 安全与合规

> ⚠️ **RateGuard 仅用于学习与技术研究。**
>
> - 使用时请遵守 OTA 服务条款和 robots.txt
> - 禁止将收集到的酒店价格数据用于商业竞争或其他不符合法律法规的用途
> - 本项目提供「价格监测与自我提醒」能力，不提供自动下单、自动改价功能
> - 使用者自行承担合规风险

---

## 路线图

- [x] **v1.0-MVP·阶段①** — 基础框架 + 配置文件 struct（当前）
- [ ] **v1.0-MVP·阶段②** — 按坐标/城市搜索 + prices 表有数据
- [ ] **v1.0-MVP·阶段③** — 规则引擎 + 飞书告警
- [ ] **v1.1** — GitHub Actions 自动定时调度
- [ ] **v1.2** — Dashboard HTML 可视化看板

---

## 许可

MIT License — 自由使用，自由修改，不负责你的操作。
