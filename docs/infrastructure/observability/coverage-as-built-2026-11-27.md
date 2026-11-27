# GMS v5 - Observability Coverage As-Built

- Evidence date: 2026-11-27 JST
- Scope: CUJ, infrastructure vitals, external availability
- Status: Implementation and query validation complete
- Configuration: Exported from production

## 1. Objective

Extend observability coverage beyond translation latency
and external availability.

The objective is to correlate user-facing symptoms with
application and infrastructure measurements before
conducting capacity and concurrency experiments.

## 2. Three-layer observability model

| Layer | Dashboard | Panels |
|---|---|---:|
| CUJ | Translation SLO | 8 |
| CUJ | Glossary Management | 6 |
| Infrastructure | Application Infrastructure | 12 |
| Infrastructure | Observability Infrastructure | 4 |
| External | External Availability | 9 |
| Total | 5 dashboards | 39 |

Grafana dashboard JSON files are stored under:

infra/observability/grafana/dashboards/

These are production snapshots, not automatically
provisioned dashboards.

## 3. Prometheus retention

Runtime configuration confirmed:

- Time retention: 10 days
- Size retention: 1 GiB

Actual retained duration may be shorter if the size
limit is reached.

Raw translation metric evidence for November 26
was preserved separately before further investigation.

## 4. CUJ coverage

### Translation

- Rolling seven-day latency SLO attainment
- Rolling seven-day P95 latency
- Successful translation volume
- Success ratio
- Selected-range diagnostic metrics

Existing latency SLO: 95% of successful translations
complete within 10 seconds over a rolling seven-day window.

Known rolling-window counter integrity issue remains open.

### Glossary management

- HTTP request volume
- HTTP 2xx ratio
- Operation and status breakdown
- Read and write P95 latency
- SQLite operation latency

No formal glossary SLO has been established.

SQLite operation metrics are not exclusively attributable
to glossary management requests.

## 5. Application infrastructure

OpenTelemetry Collector Contrib 0.165.0 was installed
on Application EC2 as an independent systemd service.

Collection interval: 30 seconds.

The existing OTLP/HTTPS IPv6 path is reused.
No Security Group or IAM permission expansion was made.

Collected metric groups:

- CPU time and load average
- Memory usage
- Filesystem usage and inode usage
- Disk throughput, operations and I/O time
- Network traffic, errors, drops and connections

Twenty host metric families were observed in Prometheus.

The dashboard contains twelve panels and fifteen
PromQL targets.

Whole NVMe devices are selected for disk I/O queries
to avoid double counting partition-level measurements.

Memory Used represents the OTel used-memory state,
not Linux MemAvailable.

## 6. Agent resource consumption

Observed after deployment:

| Measurement | Result |
|---|---|
| EC2 logical CPUs | 2 |
| EC2 total memory | 1840 MiB |
| Agent memory, initial sample | 40.1 MiB |
| Agent memory, later sample | 39.4 MiB |
| Agent cumulative CPU time, later sample | 0.336 seconds |
| Automatic restart count after configuration correction | 0 |
| systemd startup | enabled |

These are short-duration observations, not peak-load
or long-duration resource measurements.

## 7. Configuration validation

The initial agent configuration failed validation
because the internal telemetry readers list was empty
while the telemetry level was not none.

Correction:

    service:
      telemetry:
        metrics:
          level: none
          readers: []

The corrected configuration passed validation.

Host metrics subsequently reached Prometheus.

The application service remained active at the
observed verification points.

## 8. Telemetry interruption experiment

The host metrics agent alone was stopped deliberately.
The GMS application and EC2 were not stopped.

All timestamps are JST on 2026-11-27.

| Time | Event |
|---|---|
| 18:36:21 | Agent stopped |
| 18:38:42 | Last observed activity=1 |
| 18:38:57 | First observed activity=0 |
| 18:40:43 | Last observed activity=0 |
| 18:40:45 | Agent started |
| 18:40:58 | First observed recovery activity=1 |
| 18:41:13 | Continued activity=1 confirmed |

Results:

- Observed update-stop detection: 156 seconds
- Agent stop-to-start interval: 264 seconds
- Observed activity recovery: 13 seconds
- Observation interval: approximately 15 seconds

The measured times represent the first observed
state transitions, not exact internal transition times.

## 9. Dashboard query verification

All fifteen PromQL targets were evaluated at three
historical timestamps.

| State | Time JST | Result |
|---|---|---|
| Before stop | 18:36:00 | 15/15 PASS |
| During interruption | 18:39:12 | 15/15 PASS |
| After recovery | 18:41:13 | 15/15 PASS |

During interruption:

- Host Telemetry Activity returned 0.
- All other fourteen targets returned empty vectors.

After recovery:

- Host Telemetry Activity returned 1.
- All fourteen guarded targets returned metric values.

Total: 45 successful query evaluations across three
states.

This verifies the PromQL missing-data behavior.
It does not independently verify Grafana rendering.

## 10. External availability

The existing external availability dashboard
continues to use CloudWatch metrics.

- Availability SLO: 99.0%
- Window: rolling seven days
- Weekly error budget: 100.8 minutes

No new external probe or burn-rate alert was introduced.

## 11. Configuration evidence

Dashboard definitions were fetched from the live
Grafana API.

| Dashboard | Version | Panels |
|---|---:|---:|
| Translation SLO | 26 | 8 |
| Glossary Management | 1 | 6 |
| Application Infrastructure | 1 | 12 |
| Observability Infrastructure | 14 | 4 |
| External Availability | 4 | 9 |

The original host metrics configuration and systemd
unit were verified against production SHA256 checksums.

Before publication, the OTLP endpoint was replaced
with the GMS_OTLP_ENDPOINT environment variable.
The original verified configuration is retained
outside the public repository.
The systemd unit remains byte-identical to production.
The public configuration variant has not been deployed.

Original production config.yaml:

bf19de4d52e704e54198974335f0ab81673a8d9da733d2a19f2cf7b59d148a48

gmsv5-hostmetrics.service:

3c09b08c3c1ae2fa867f68082d012943fa6423bfe30ad09f3e804044ec7d7f74

Collector artifact checksums are recorded separately
in artifact-manifest.json.

Credentials, private keys and binary artifacts are
excluded from source control.

## 12. Remaining work

- Grafana visual verification.
- Burn-rate alert design and historical backtesting.
- Translation rolling-window integrity investigation.
- Translation outcome classification and SLI definition.
- Capacity and concurrency experiments.
- Long-duration host metrics agent resource measurement.
- Spot interruption recovery verification.
- Dedicated heartbeat or stronger freshness detection.
- Full Observability EC2 configuration management.
- Automated dashboard provisioning.
- Host metrics recovery exercise on a replacement EC2.

The current telemetry activity heuristic can detect
a lack of CPU counter movement, but cannot establish
independent freshness for every metric family.

Rate-based queries may also be affected by cached
samples and gaps around telemetry interruptions.

The current implementation is a verified monitoring
foundation, not a completed capacity or resilience test.
