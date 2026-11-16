# GMS v5 — Translation SLO / Grafana → Discord Alerting Runbook

- **As-Built:** [2026-11-16](../observability-as-built-2026-11-16.md)
- **Owner:** Infrastructure / SRE
- **Status:** Pilot operation. Notification Firing/Resolved tested; full 7d attainment and disaster recovery NOT yet proven.

## 1. Service-level objective

**Objective:** 過去7日間の**成功した**サーバー側翻訳処理（DeepL + OpenAI post-edit）について、95%以上が10秒以内（inclusive）で完了する。Browser RUM/paintは本SLIに含まない。`gms.translation.duration` Histogramを使用。

- `good`: `gms_translation_duration_seconds_bucket{gms_translation_outcome="success",le="10.0"}` の増分。
- `eligible`: `gms_translation_duration_seconds_count{gms_translation_outcome="success"}` の増分。
- `SLI = 100 * good / eligible`。7d windowは現在から遡る移動窓。
- 成功以外のOutcomeはこのLatencyの分母に入らない。**Success Rateを別パネルで見る**。すべて失敗した場合に本Ruleが沈黙し得るため別Alertが必要。
- 最低20成功件数はPilot運用上の警報抑止値。7日間の統計的確信を意味しない。成功0件やTelemetry欠落時は「良好」ではなく「未評価 / blind spot」。

### Useful PromQL

```promql
# Attainment percentage
100 * sum(increase(gms_translation_duration_seconds_bucket{gms_translation_outcome="success",le="10.0"}[7d]))
/ sum(increase(gms_translation_duration_seconds_count{gms_translation_outcome="success"}[7d]))
```

```promql
# Approximate successful count during 7d
sum(increase(gms_translation_duration_seconds_count{gms_translation_outcome="success"}[7d]))
```

```promql
# p95 estimate, NOT the formal objective
histogram_quantile(0.95, sum by (le) (increase(gms_translation_duration_seconds_bucket{gms_translation_outcome="success"}[7d])))
```

```promql
# Overall success rate (separate from latency SLO)
100 * sum(increase(gms_translation_duration_seconds_count{gms_translation_outcome="success"}[7d]))
/ sum(increase(gms_translation_duration_seconds_count[7d]))
```

Grafana-managed Alert: `GMS Translation SLO Breach — Rolling 7d`。5m eval, 10m pending, 0 keep-firing。以下のBoolean判定Query AをThreshold `>0.5`で評価する。

```promql
(
  (
    100 *
    (sum(increase(gms_translation_duration_seconds_bucket{gms_translation_outcome="success",le="10.0"}[7d])) or vector(0)) /
    clamp_min((sum(increase(gms_translation_duration_seconds_count{gms_translation_outcome="success"}[7d])) or vector(0)), 1)
    < bool 95
  )
  *
  ((sum(increase(gms_translation_duration_seconds_count{gms_translation_outcome="success"}[7d])) or vector(0)) >= bool 20)
)
```

**Caution:** `NaN` was observed when the first cumulative Count was already 1 on the initial Scrape and `increase(...[7d])=0`. `count_over_time` counts Scrapes, not translations. The formal SLI panel should show N/A with insufficient increment; Boolean rule is intentionally non-firing under insufficient data, not evidence of health.

## 2. Alert delivery flow

```text
Grafana container (gms-observability bridge IPv4, observed 172.18.0.6)
 → POST http://172.18.0.1:18081/notify
 → Nginx gms-discord-egress (--network host, binds bridge gateway only)
 → HTTPS IPv6 d17p4dgtsvz5w4.cloudfront.net/notify
 → CloudFront Function (KVS relay-token, webhook-path)
 → Discord Webhook origin
 → Discord server GMS v5 Operations, #gms-alerts
```

Contact Point name `GMS SRE Discord Alerts`, integration `Discord`, URL uses **local Relay** not Discord's actual webhook. `disableResolveMessage=false`。Grafana UI is accessed through SSM port forwarding at `localhost:3000`; links in Discord notifications containing `localhost` do not work from iPhone without an appropriate SSM tunnel. GrafanaをPublic公開して対処しない。

## 3. Initial triage when an SLO alert fires

1. GrafanaからRule `GMS Translation SLO Breach — Rolling 7d` のFiring/HistoryとQuery Aを確認し、`Volume`（成功件数）が20以上か検証。
2. Dashboardを開き、Rolling 7d Attainment / p95 / Success Volume / Success Rateを確認。AlertのBoolean 1だけで原因判断しない。
3. Prometheus Targets `up` を確認。`gms_translation_duration_seconds_count` が伸びているか、Telemetryが止まっていないか検証。
4. Grafana Tempoで`gms.translation.request`、`gms.deepl.translate`、`gms.openai.post_edit` を確認（Tempo retentionは24h）。DeepLとOpenAIの内訳を比較する。
5. エラーレート、API依存性、Cost、Quotaを確認。大量アクセスで闇雲に再現しない。翻訳原文、APIキー、ユーザー識別子はログ・報告書へ記録しない。
6. 影響期間、件数、対応、復旧時点をIncident Noteに記録。7d窓のため、最近の状態が回復しても過去の違反でAlertが長く残り得る。

## 4. Read-only diagnostic commands (Observability EC2 via SSM)

```bash
# Current effective retention and health
curl -fsS http://127.0.0.1:9090/api/v1/status/runtimeinfo \
  | python3 -c 'import json,sys; x=json.load(sys.stdin)["data"]; print("retention=",x.get("storageRetention"), "reload=",x.get("reloadConfigSuccess"))'
curl -fsS http://127.0.0.1:9090/-/ready
sudo docker ps --format 'table {{.Names}}\t{{.Status}}'

df -h /
```

For Prometheus instant API calls (no Grafana credentials needed on the host-local 9090 listener):

```bash
q() {
  curl -fsSG 'http://127.0.0.1:9090/api/v1/query' --data-urlencode "query=$1" \
  | python3 -c 'import json,sys; d=json.load(sys.stdin); print("status:",d.get("status")); [(print(x["metric"],"=",x["value"][1])) for x in d.get("data",{}).get("result",[])]'
}
q 'sum by(job) (up)'
q 'sum(increase(gms_translation_duration_seconds_count{gms_translation_outcome="success"}[7d]))'
q 'prometheus_tsdb_retention_limit_seconds'
q 'prometheus_tsdb_retention_limit_bytes'
```

## 5. Alert delivery diagnostics

- If Grafana contact point test fails: check `sudo docker ps --filter name=gms-discord-egress` and `sudo ss -ltnp '( sport = :18081 )'`; confirm the Grafana container's current IPv4 via `sudo docker inspect gms-grafana --format '{{range .NetworkSettings.Networks}}{{.IPAddress}}{{end}}'` matches Nginx `allow` policy. Do **not** log the Nginx config: it contains a Secret.
- Test cloud origin from Grafana only when a real test notification is intended; this sends an actual Discord message:

```bash
sudo docker exec gms-grafana curl --noproxy '*' -fsS \
  -X POST -H 'Content-Type: application/json' \
  --data '{"content":"GMS v5: relay manual test","allowed_mentions":{"parse":[]}}' \
  -o /dev/null -w 'HTTP=%{http_code}\n' \
  http://172.18.0.1:18081/notify
```

- `HTTP 204` plus message visible in `#gms-alerts` = endpoint-to-endpoint delivery PASS。`403` = Nginx ACLまたはCloudFront Function認証、`502` = Nginx Origin TLS/DNS/CloudFront Origin、`429` = Discord rate limiting可能性。認証Tokenを出力しない。
- If Contact Point succeeds but alerts do not arrive: verify Rule state (Firing vs Pending), 10m pending, notification policy, Contact Point association, `disableResolveMessage=false`, and Grafana logs (secrets redacted).
- `GMS Test — Discord Firing Resolved` was removed after successful `vector(1)` Firing and `vector(0)` Resolved testing. Do not leave `vector(1)` alerts enabled in production.

## 6. Restart, persistence and backups

**Known not solved:** Prometheus container startup flags recorded as `3d / 512MB` before retention reload, although runtime after reload was `10d / 1GiB`. After reboot/recreate verify both settings, rather than assuming persistence. The alert relay uses `--restart unless-stopped`, but that does not replace provisioning of `/opt/gmsv5-observability/discord-relay/default.conf` and the Secret. Do not intentionally stop the Spot instance just to test without having backups.

- Terraform separate root: `infra/terraform/discord-alert-egress/`. Check CloudFront distribution, Function/KVS, Origin Request Policy with `terraform plan` before deployment. Independent Local State must be backed up securely outside Git.
- KVS entries `relay-token` and `discord-webhook-path` were manually inserted. Keys/values are **not** recreated just by Terraform apply. Store sensitive values separately in protected secret storage and document how to repopulate without displaying them.
- Nginx config at `/opt/gmsv5-observability/discord-relay/default.conf` is root-only, contains `X-GMS-Notify-Token` and must **not** be committed to Git. A redacted template is provided under `infra/observability/discord-relay/` for reference.
- Grafana Dashboard exports (V2 and Classic), SLO Alert YAML, and Contact Point YAML can be versioned after review. A Grafana Contact Point export must not contain a real Discord webhook URL or tokens; here it should point to the bridge gateway only.
- Grafana database, Prometheus TSDB and Tempo files are on a 10GiB Root EBS with no verified restore path. Root EBS on termination is delete-on-termination: Spot restart and Spot replacement have different persistence implications. No claim of full recovery until restored and retested.
- **DNS/security caveat:** CloudFront Function stores Discord Webhook path containing its Token in KVS. Audit CloudFront logging for post-rewrite URI leaks, KVS IAM permissions, and rotate secrets on compromise.

## 7. Synthetic trial plan (not performed as of 2026-11-16)

A single existing GMS account can run modest, spaced-out translations to add data without identifying individual users in Traces. Bot automation requires its own permission and rate/cost budgeting; do not share credentials with external bots unless the service terms and personal security risk are accepted.

Suggested measurement strata: short without terms, short with 1–2 terms, medium with terms, longer texts, and infrequent sequential repeats. Record local time, text length (not content), number of selected terms, success/failure, displayed outcome, and client-side perceived time (if manually measured) in a private test ledger. Do **not** attribute per-test values to Prometheus aggregate buckets without independent correlation evidence. Use only authorized testing on this own application, avoid large concurrency and deliberate API quota exhaustion.

For a 7-day trial begun on Nov 16 JST, review on/after Nov 23 JST. Note 10d-or-1GiB retention is conditional on actual storage availability. Quantify Volume, Attainment, Success Rate, observed conditions and telemetry gaps; report insufficient samples as an inconclusive result, not PASS.

## 8. Incident-specific Prometheus recovery procedure

**Incident:** INC-20261116-PROM-RESTART

### 8.1 Restart policy

Prometheus was configured with `unless-stopped` before the incident.
After it remained stopped following an EC2 restart, the operator
changed the policy to `always`.

Current verified setting: `Restart=always Status=running`.

### 8.2 Safe configuration reload

Avoid `docker kill --signal=HUP gms-prometheus` because of the
observed manual-stop flag and a possible Docker signal-handling issue.

Send SIGHUP directly to the running Prometheus process:

~~~bash
PID="$(sudo docker inspect -f '{{.State.Pid}}' gms-prometheus)"
test "$PID" -gt 1 && sudo kill -HUP "$PID"
~~~

Verify configuration and readiness:

~~~bash
curl -fsS http://127.0.0.1:9090/api/v1/status/runtimeinfo

sudo docker inspect gms-prometheus \
  --format 'Restart={{.HostConfig.RestartPolicy.Name}} Status={{.State.Status}}'

curl -fsS http://127.0.0.1:9090/-/ready
~~~

Expected: successful reload, `10d or 1GiB` retention,
`Restart=always`, and Prometheus Ready.

### 8.3 Recovery after unexpected shutdown

1. Check `sudo docker ps -a` before modifying containers.
2. Check the EC2 boot history and Docker journal.
3. Check whether `gms-prometheus` is stopped.
4. If stopped, preserve evidence and start the existing container.
5. Verify Grafana-to-Prometheus readiness.
6. Verify the four Prometheus scrape jobs.
7. Check retention and the restart policy.
8. Verify Grafana alert recovery and Discord notification.
9. Record the incident timeline and any missing metrics.

Do not remove or recreate Prometheus containers or volumes as an
initial troubleshooting step.

### 8.4 Limitations

The manual recovery and new restart policy were verified on
2026-11-16. Automatic restart after a subsequent EC2 reboot
and complete recovery after Spot instance replacement
have not been tested.
