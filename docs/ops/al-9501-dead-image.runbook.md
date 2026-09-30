# al-9501-dead-image

class: **propose** (not auto). One clean restart does not graduate this id.

Fingerprint: monitoring-api logs `No such image` for `images/<sha>/json` while `GET /health` is 200.

Probe: `docker logs monitoring-api --since 30m` and keep the `No such image` line. Do not treat `/health` 200 as the receipt.

Fix: label containers from the live inspect `Config.Image`. Do not call Docker SDK `container.image` (that GETs the content id). Landed on razzorstack `abf8ed2`.

Verify: the next monitor pass logs a healthy line and no new `No such image`. Record the restart time if a process restart was required to load the script.

GO shape for a deploy or absorb of this fingerprint: `GO: al-9501 fp=al-9501 lane=razzorstack exp=<ISO-8601 Z>`. A bare absorb-and-deploy line is refused.
