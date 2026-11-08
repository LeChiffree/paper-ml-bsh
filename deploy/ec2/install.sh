#!/usr/bin/env bash
set -euo pipefail

if [[ "${EUID}" -ne 0 ]]; then
  echo "Run as root: sudo bash deploy/ec2/install.sh" >&2
  exit 1
fi

APP_ROOT=/opt/paper-ml
APP_USER=paperml

test -f "${APP_ROOT}/paper_trade_app/requirements.txt"
test -f "${APP_ROOT}/.env"

apt-get update
DEBIAN_FRONTEND=noninteractive apt-get install -y python3 python3-venv python3-pip nginx

if ! id "${APP_USER}" >/dev/null 2>&1; then
  useradd --system --home-dir "${APP_ROOT}" --shell /usr/sbin/nologin "${APP_USER}"
fi

python3 -m venv "${APP_ROOT}/.venv"
"${APP_ROOT}/.venv/bin/pip" install --upgrade pip
"${APP_ROOT}/.venv/bin/pip" install -r "${APP_ROOT}/paper_trade_app/requirements.txt"

install -d -o "${APP_USER}" -g "${APP_USER}" "${APP_ROOT}/paper_trade_app/data" "${APP_ROOT}/paper_trade_app/logs"
chown -R "${APP_USER}:${APP_USER}" "${APP_ROOT}"
chmod 600 "${APP_ROOT}/.env"

install -m 0644 "${APP_ROOT}/deploy/ec2/paper-ml.service" /etc/systemd/system/paper-ml.service
install -m 0644 "${APP_ROOT}/deploy/ec2/nginx-paper-ml.conf" /etc/nginx/sites-available/paper-ml
ln -sfn /etc/nginx/sites-available/paper-ml /etc/nginx/sites-enabled/paper-ml
rm -f /etc/nginx/sites-enabled/default

nginx -t
systemctl daemon-reload
systemctl enable --now paper-ml nginx

curl --fail --silent --show-error http://127.0.0.1:8765/healthz
echo
echo "Paper ML is running. Open the EC2 security group only on TCP 80 (and TCP 22 from your IP)."
