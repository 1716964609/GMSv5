# P2: continuous failure alarm (6 of 6), next to the 9-of-60 burn rate alarm.
# Same metric, same delivery path (EventBridge -> existing Lambda ->
# existing Discord CloudFront egress), its own rule, rollout and state row.
#
# treat_missing_data = "missing" and the default sliding window, chosen from
# the isolated integration test of 2026-11-28 (Availability Runbook §14.2):
# - FILL(m1, 1) and the wall clock window break a run at a missing minute
#   (strict), but never alarmed when datapoints arrived 130 s or more late.
# - Sliding window: CloudWatch evaluates the 6 most recent real datapoints in
#   an evaluation range of about 12 minutes, so failures on both sides of a
#   missing minute can alarm (not strictly 6 wall-clock minutes).
# - "missing" leaves ALARM only on a real passed check, and goes to
#   INSUFFICIENT_DATA (not OK) about 12 minutes after the probe stops.
locals {
  continuous_failure_alarm_name = "gmsv5-availability-continuous-failure"
  continuous_failure_alarm_arn  = "arn:aws:cloudwatch:${var.aws_region}:${data.aws_caller_identity.current.account_id}:alarm:${local.continuous_failure_alarm_name}"
}

# Rollout gate: variable continuous_failure_alarm_rollout (variables.tf).

resource "aws_cloudwatch_metric_alarm" "continuous_failure" {
  alarm_name        = local.continuous_failure_alarm_name
  alarm_description = "HTTPUp: the 6 most recent one-minute samples failed (missing minutes are skipped). Notifications via EventBridge -> gmsv5-availability Lambda. Runbook: docs/infrastructure/availability-external-runbook.md section 14."
  namespace         = "GMS/Availability"
  metric_name       = "HTTPUp"
  dimensions        = { Target = "sunlightjetrans.com" }
  statistic         = "Minimum"
  period            = 60
  # No unit filter: the probe publishes Count, and a mismatch would hide data.
  evaluation_periods  = 6
  datapoints_to_alarm = 6
  threshold           = 1
  comparison_operator = "LessThanThreshold"
  treat_missing_data  = "missing"
  # No SNS or other actions. Delivery is the EventBridge rule below.
  alarm_actions             = []
  ok_actions                = []
  insufficient_data_actions = []
  tags                      = { Project = "GMSv5", Owner = "SRE" }
}

# Only this alarm's state changes, matched by source, type, ARN and name.
resource "aws_cloudwatch_event_rule" "continuous_failure" {
  name  = "gmsv5-availability-continuous-failure"
  state = var.continuous_failure_alarm_rollout == "off" ? "DISABLED" : "ENABLED"
  event_pattern = jsonencode({
    source        = ["aws.cloudwatch"]
    "detail-type" = ["CloudWatch Alarm State Change"]
    resources     = [aws_cloudwatch_metric_alarm.continuous_failure.arn]
    detail        = { alarmName = [aws_cloudwatch_metric_alarm.continuous_failure.alarm_name] }
  })
}

resource "aws_cloudwatch_event_target" "continuous_failure" {
  rule = aws_cloudwatch_event_rule.continuous_failure.name
  arn  = aws_lambda_function.availability.arn
}

resource "aws_lambda_permission" "continuous_failure" {
  statement_id  = "AllowContinuousFailureAlarmEvents"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.availability.function_name
  principal     = "events.amazonaws.com"
  source_arn    = aws_cloudwatch_event_rule.continuous_failure.arn
}

output "continuous_failure_alarm_name" { value = aws_cloudwatch_metric_alarm.continuous_failure.alarm_name }
