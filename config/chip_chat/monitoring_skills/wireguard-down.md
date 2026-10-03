# wireguard-down

Live VPN is the **wireguard** container (`wg0`). **Never** restart or recreate **gluetun**.

Fix law:
- Record the first alert sighting; run a container fix only after **10 minutes** continuous down-class alerts.
- Allowed pm fix after soak: `docker restart wireguard` only when inspect shows it is not running.
- Receipts stay in `#monitoring-logs` only.
