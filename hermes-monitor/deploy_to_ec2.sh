#!/usr/bin/env bash
set -euo pipefail

HOST="${HERMES_DEPLOY_HOST:?Set HERMES_DEPLOY_HOST}"
KEY="${HERMES_DEPLOY_KEY:?Set HERMES_DEPLOY_KEY}"
REMOTE_DIR="${HERMES_DEPLOY_DIR:?Set HERMES_DEPLOY_DIR}"
SESSION="hermes"

scp -i "$KEY" hermes_monitor.py hermes_api.py hermes_detail_sweeper.py README.md "$HOST:$REMOTE_DIR/"
ssh -i "$KEY" "$HOST" "set -euo pipefail
cd '$REMOTE_DIR'
python3 -B -c 'import hermes_monitor; print(\"import_ok\")'
python3 hermes_monitor.py --list-products | head -5
if tmux has-session -t '$SESSION' 2>/dev/null; then
  tmux kill-session -t '$SESSION'
fi
tmux new-session -d -s '$SESSION' 'cd $REMOTE_DIR && set -a && source .env && set +a && exec python3 hermes_monitor.py'
if tmux has-session -t hermes-api 2>/dev/null; then
  tmux kill-session -t hermes-api
fi
if tmux has-session -t hermes-detail 2>/dev/null; then
  tmux kill-session -t hermes-detail
fi
tmux new-session -d -s hermes-api 'cd $REMOTE_DIR && set -a && source .env && set +a && exec python3 hermes_api.py --host 127.0.0.1 --port 8765'
tmux new-session -d -s hermes-detail 'cd $REMOTE_DIR && set -a && source .env && set +a && exec python3 hermes_detail_sweeper.py --loop'
sleep 3
tmux capture-pane -pt '$SESSION' -S -20
tmux capture-pane -pt hermes-api -S -5
tmux capture-pane -pt hermes-detail -S -8
pgrep -af 'python3 hermes_monitor.py|python3 hermes_api.py|python3 hermes_detail_sweeper.py'
"
