# EC2 deployment

Recommended baseline: Ubuntu 24.04 LTS x86_64, 4 vCPU, 8 GiB RAM, and a 50 GiB gp3 volume.

## Security group

- TCP 22: only the operator's fixed IP.
- TCP 80: dashboard viewers. Prefer a fixed allow-list when possible.
- Do not expose port 8765; Nginx reaches it over localhost.

Add a domain and TLS before exposing the dashboard broadly. The application already requires HTTP Basic authentication, but plain HTTP does not encrypt that password.

## First installation

Copy the repository to `/opt/paper-ml`. Copy `.env` separately; never put it in an archive or Git repository. Then run:

```bash
cd /opt/paper-ml
sudo bash deploy/ec2/install.sh
```

Useful checks:

```bash
systemctl status paper-ml --no-pager
journalctl -u paper-ml -f
curl http://127.0.0.1:8765/healthz
```

## Stateful migration

Only one engine may run at a time. Stop the local service before the final database copy, transfer `paper_trade_app/data/paper_trade.db`, and then start the EC2 service. This avoids duplicated signals and orders. Back up the database and `.env` before every replacement.

The service writes under `paper_trade_app/data` and `paper_trade_app/logs`. Configure an EBS snapshot and log rotation for a month-long run.
