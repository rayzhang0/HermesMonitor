# Hermes Monitor

Tracks product links on the Hermes women's bags category page and records availability sessions.

Target page:
https://www.hermes.com/us/en/category/leather-goods/bags-and-clutches/womens-bags-and-clutches/#|

## Current Logic

The monitor only reads the main category page. It does not open second-layer product detail pages. A product link appearing on the main page is treated as available; when that link disappears, the open availability session is closed.

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

Run continuously every 15 minutes plus jitter:

```bash
python3 hermes_monitor.py --interval 900 --jitter 300
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
