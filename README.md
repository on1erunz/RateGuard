# RateGuard

[简体中文](README.zh-CN.md)

RateGuard is a local-first hotel price monitoring tool for fixed Ctrip hotel-detail URLs. It opens the normal Ctrip website in Playwright using a user-created login session, extracts available **room type + nightly price** plans, stores observations locally, and sends Feishu/Lark alerts when a comparable price moves by a configured threshold.

It is designed for monitoring properties you operate or are authorized to monitor. It is not an official Ctrip tool and does not provide automated booking, price changes, API replay, or bypass mechanisms.

## What it does

- Monitors fixed Ctrip hotel-detail URLs.
- Defaults to no-breakfast plans; meal and cancellation terms are not stored or shown.
- Saves local SQLite history and raw response artifacts.
- Compares the same hotel, room plan, and check-in date against the previous run.
- Sends Feishu/Lark alerts for price changes at or above the configured amount, plus one no-change or error status per completed monitoring cycle.
- Runs on a Windows schedule: hourly for current-day monitoring and twice daily for future-date anchors.
- Exports a sanitized dashboard snapshot plus a normalized future-room-price JSON sheet for price planning.

## Architecture

```text
Windows PC (scheduled Playwright collection)
  ├─ local SQLite + raw captures
  ├─ Feishu/Lark price alerts
  └─ sanitized dashboard.json ──> Vercel Blob ──> Vercel dashboard
```

The collector stays on your own computer because it uses your browser login state. The dashboard is optional and can be hosted separately.

## Requirements

- Windows 10/11 (the scheduler scripts use Windows Task Scheduler)
- Python 3.10+
- Node.js 20+ and `npx` (only for publishing dashboard data to Vercel)
- A Ctrip account that can log in normally in a browser

## Initial setup

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

Edit `configs/config.yaml`:

1. Add your hotel and competitor Ctrip URLs under `ctrip_mvp.targets`.
2. Mark your own property with `role: own` to show its lowest price as the dashboard reference price.
3. Set `alert_threshold_yuan` and the no-breakfast filter as required.

Edit `.env` only if Feishu/Lark alerts are required:

```dotenv
RATEGUARD_LARK_WEBHOOK=https://open.feishu.cn/open-apis/bot/v2/hook/...
```

## Log in and run once

```powershell
# Opens a browser. Complete the normal Ctrip login, then follow the prompt.
python -m src.ctrip_mvp --login

# Collect all configured hotels for one check-in date.
python -m src.ctrip_mvp --checkin 2026-07-20
```

The local login state is saved in `.secrets/ctrip_state.json`. It is deliberately ignored by Git. If the Ctrip session expires, run the login command again.

## Schedule

Install the Windows scheduled tasks after the first successful collection:

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\install_schedule.ps1
```

The supplied schedule is:

| Time | Work |
| --- | --- |
| 08:00–23:00 | Hourly collection for the current check-in date |
| 00:00 | Future anchors: tomorrow, this Friday, next Monday, next Friday |
| 12:00 | Same future-anchor collection |

At 12:00, the previous check-in date is removed from the **current-price** view but remains in local history and the dashboard history tab. The PC must be powered on, connected to the network, and the scheduled-task user must remain signed in.

When no room-plan price changes meet `alert_threshold_yuan`, Feishu receives one **No price changes** completion message. The four future anchor dates are summarized in one message. If a cycle has collection errors, Feishu receives an error status instead.

## Vercel dashboard (optional)

Deploy the dashboard site once:

```powershell
cd vercel-dashboard
npm install
npx vercel --prod
```

Create and connect a Vercel Blob store for the Vercel project, then copy `vercel-dashboard/.env.example` to `vercel-dashboard/.env.local` and supply the Blob read/write token. Keep that file private.

After each local collection, publish the sanitized dashboard snapshot:

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\publish_dashboard.ps1
```

The scheduled job calls the same publishing script automatically. Uploads time out after 90 seconds so a stalled dashboard sync cannot block later collections. The dashboard header includes **Download future room prices JSON**, which downloads the latest future-date sheet (`/api/future-room-prices`). It is grouped by check-in date and hotel, then lists every collected no-breakfast room plan with its nightly price, availability, and update time.

## Data and safety

Never commit or publish these files:

- `.env` and `vercel-dashboard/.env.local`
- `configs/config.yaml`
- `.secrets/`
- `db/`, `logs/`, and `output/`

Use the provided `*.example` files as templates. If a token, webhook, or login state is accidentally exposed, revoke or rotate it immediately.

The project exports only display-safe fields to the dashboard. Raw Ctrip responses and browser captures stay local.

## Useful commands

```powershell
# Export a new local dashboard snapshot from SQLite
python -m src.dashboard_export

# Run the scheduled logic manually
python -m src.scheduled_run --mode hourly
python -m src.scheduled_run --mode anchors

# Start the local Streamlit dashboard
streamlit run gui/app.py
```

## Limitations

- Ctrip may change its website, login flow, response format, or availability rules.
- Collection requires a valid login session and can require manual re-login.
- The current implementation supports fixed Ctrip hotel URLs. Meituan collection is intentionally out of scope.
- You are responsible for complying with applicable law and the platform's terms.

## License

[MIT](LICENSE)
