# Application EC2 Host Metrics

## Purpose

Collect Application EC2 host metrics independently of the
GMS v5 application process.

Architecture:

Host Metrics Receiver
-> OTLP/HTTPS over IPv6
-> Observability EC2 Nginx Gateway
-> Central OTel Collector
-> Prometheus
-> Grafana

No additional inbound port or IAM permission is required
under the current production configuration.

## Components

- OpenTelemetry Collector Contrib 0.165.0
- Linux ARM64 / Amazon Linux 2023
- Collection interval: 30 seconds
- Dedicated non-root systemd service
- Binary and archive checksums: artifact-manifest.json

## Recovery procedure

1. Retrieve the pinned archive from the existing release
   S3 bucket using the regional dualstack endpoint.
2. Verify its SHA256 against artifact-manifest.json.
3. Extract the otelcol-contrib binary and verify its SHA256.
4. Install the binary under /opt/gmsv5-hostmetrics/.
5. Create the dedicated gms-hostmetrics system user.
6. Install config.yaml under /etc/gmsv5-hostmetrics/.
7. Install the trusted telemetry CA as ca.pem.
8. Provision agent.env with GMS_OTLP_TOKEN and
   GMS_OTLP_ENDPOINT (the trusted OTLP/HTTPS endpoint).
   The file must be owned by root with mode 0600.
   Retrieve the credential through the existing secure
   provisioning process. Never commit or print its value.
9. Verify the endpoint and instance ID against the
   recovered infrastructure. Update them if necessary.
10. Install the systemd unit and validate the configuration
    before enabling or starting the service.
11. Verify host metrics in Prometheus and Grafana.

The repository contains a public-safe derivative of
the verified production configuration.
The production OTLP endpoint is parameterized using
GMS_OTLP_ENDPOINT to avoid publishing its address.
The systemd unit is unchanged from production.
This is not an automated installation system.

## Configuration validation

    sudo systemd-run --wait --pipe --collect \
      -p User=gms-hostmetrics \
      -p Group=gms-hostmetrics \
      -p EnvironmentFile=/etc/gmsv5-hostmetrics/agent.env \
      /opt/gmsv5-hostmetrics/otelcol-contrib \
      validate \
      --config=/etc/gmsv5-hostmetrics/config.yaml

Proceed only if validation exits successfully.

## Service operations

    sudo systemctl daemon-reload
    sudo systemctl enable --now gmsv5-hostmetrics
    sudo systemctl status gmsv5-hostmetrics
    sudo journalctl -u gmsv5-hostmetrics --since "5 minutes ago"

To stop the agent independently of GMS:

    sudo systemctl stop gmsv5-hostmetrics

To restore collection:

    sudo systemctl start gmsv5-hostmetrics

## Resource limits

- CPUQuota: 25% of one logical CPU
- MemoryHigh: 128 MiB
- MemoryMax: 192 MiB
- GOMEMLIMIT: 96 MiB
- Collector memory limiter: 80 MiB
- Export retry max elapsed time: 120 seconds

## Telemetry identification

- service.name: gmsv5-hostmetrics
- gms_host_role: application
- exported_instance: production EC2 instance ID

The dashboard uses CPU counter movement over three
minutes as a telemetry activity heuristic.

Prometheus scrape success alone does not establish
that the remote host is still exporting metrics.

## Credential and certificate rotation

The agent uses a separately provisioned copy of the
OTLP token and CA certificate.

When these are rotated, update both application and
host metrics agent configurations as appropriate.
Validate the agent configuration before restarting it.

The certificate currently used by the gateway expires
on 2027-02-11.

## Known limitations

- Configuration contains environment-specific identifiers.
- Recovery from a newly provisioned EC2 is not yet tested.
- Automatic recovery after Spot interruption is not tested.
- Telemetry activity is a heuristic, not a heartbeat.
- NVMe device mappings must be verified after rebuilding EC2.
- The export queue is not persistent across agent restarts.
- Dashboard provisioning remains manual/API-based.
