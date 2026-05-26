# Hermes Monitor

Tracks product links on the Hermes women's bags category page and records availability sessions.

Target page:
https://www.hermes.com/us/en/category/leather-goods/bags-and-clutches/womens-bags-and-clutches/#|

## Current Logic

The main monitor reads the category page and treats a product link appearing there as visible. When that link disappears, the open availability session is closed. A separate detail sweeper checks visible product pages to classify purchasable status.

The category checker uses its own IPv4 request queue. The detail sweeper uses its own IPv6 request queue. Each queue keeps a separate minimum request gap and separate rate-limit/recovery alert state so detail-page checks do not delay the main category check.

Because the current page contains stale product links, the first successful run after this version seeds the current active links as a one-time excluded baseline. Those baseline products do not appear as available in the app and do not create false alert emails. If a baseline product disappears and later reappears, that later appearance is tracked as a real availability session.

Tracked fields:

- Product name
- Product URL
- Price
- Color when visible
- Image URL when extractable from the category page
- Available from / available until

## Commands

Load environment:

```bash
set -a
source .env
set +a
```

Initialize the current live listing as the excluded baseline:

```bash
python3 hermes_monitor.py --init
```

Run once:

```bash
python3 hermes_monitor.py --once
```

Run continuously every 5 minutes plus jitter:

```bash
python3 hermes_monitor.py --interval 300 --jitter 120
```

Export app-readable JSON:

```bash
python3 hermes_monitor.py --export-json
```

List currently tracked non-baseline available products:

```bash
python3 hermes_monitor.py --list-products
```

## Alerts

Inventory/change emails go to `HERMES_EMAIL_TO`. Emails include only newly added, removed, price-changed, or detail-changed products.

Operational failure and recovery emails go to `HERMES_FAILURE_EMAIL_TO`.

Inventory/change alerts are also sent as iOS push notifications to registered app devices when APNs is configured:

```bash
export HERMES_APNS_TEAM_ID=...
export HERMES_APNS_KEY_ID=...
export HERMES_APNS_BUNDLE_ID=com.kingstonai.hermesmonitor
export HERMES_APNS_AUTH_KEY_PATH=/home/ec2-user/hermes-monitor/AuthKey_XXXXXXXXXX.p8
export HERMES_APNS_ENV=production
```

The iOS app registers device tokens at `/push/register`. Push test calls can use `/push/test`; set `HERMES_PUSH_ADMIN_TOKEN` to require the `X-Hermes-Push-Test-Token` header.

Before archiving a device build, enable Push Notifications for the `com.kingstonai.hermesmonitor` App ID in Apple Developer and refresh the provisioning profile. Without that capability, Xcode will reject the `aps-environment` entitlement during device/archive signing.

## iOS App Feed

The monitor exports `state/public_inventory.json` with:

- `available`: currently visible non-baseline products
- `history`: recent availability sessions

The iOS app can read this JSON from a hosted URL configured in the app's Account tab.

## Deployment Configuration

`deploy_to_ec2.sh` expects the deployment target to be provided at runtime instead of storing infrastructure details in the repository:

```bash
export HERMES_DEPLOY_HOST=ec2-user@example-host
export HERMES_DEPLOY_KEY="$HOME/.ssh/example-key"
export HERMES_DEPLOY_DIR=/home/ec2-user/hermes-monitor
```
