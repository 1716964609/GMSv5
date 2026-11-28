# P2: external availability burn rate alarm (fast window).
# CloudWatch evaluates HTTPUp natively; the existing Lambda formats the state
# changes and sends them through the existing Discord CloudFront egress.
# Neither EC2 instance is in the evaluation or delivery path.
#
# SLO 99 %, rolling 7 days: budget 100.8 min/week. On a full 60-sample window,
# 9 failed samples = 15x burn rate, the first value above the 14.4x design
# threshold; 8 failed samples = 13.3x stays OK.
# With treat_missing_data = "missing", CloudWatch can look back past 60 minutes
# when datapoints are missing, so it is not identical to the offline backtest
# (Availability Runbook §13.2).
locals {
  burn_rate_alarm_name = "gmsv5-availability-burn-rate-fast"
}

# Rollout gate: variable burn_rate_alarm_rollout (variables.tf).

resource "aws_cloudwatch_metric_alarm" "burn_rate_fast" {
  alarm_name        = local.burn_rate_alarm_name
  alarm_description = "HTTPUp: >= 9 of the last 60 one-minute samples failed (15x burn rate of a 99% / 7d SLO). Notifications via EventBridge -> gmsv5-availability Lambda. Runbook: docs/infrastructure/availability-external-runbook.md section 13."
  namespace         = "GMS/Availability"
  metric_name       = "HTTPUp"
  dimensions        = { Target = "sunlightjetrans.com" }
  statistic         = "Minimum"
  period            = 60
  # No unit filter: the probe publishes Count, and a mismatch would hide data.
  evaluation_periods  = 60
  datapoints_to_alarm = 9
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
resource "aws_cloudwatch_event_rule" "burn_rate" {
  name  = "gmsv5-availability-burn-rate"
  state = var.burn_rate_alarm_rollout == "off" ? "DISABLED" : "ENABLED"
  event_pattern = jsonencode({
    source        = ["aws.cloudwatch"]
    "detail-type" = ["CloudWatch Alarm State Change"]
    resources     = [aws_cloudwatch_metric_alarm.burn_rate_fast.arn]
    detail        = { alarmName = [aws_cloudwatch_metric_alarm.burn_rate_fast.alarm_name] }
  })
}

resource "aws_cloudwatch_event_target" "burn_rate" {
  rule = aws_cloudwatch_event_rule.burn_rate.name
  arn  = aws_lambda_function.availability.arn
}

resource "aws_lambda_permission" "burn_rate" {
  statement_id  = "AllowBurnRateAlarmEvents"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.availability.function_name
  principal     = "events.amazonaws.com"
  source_arn    = aws_cloudwatch_event_rule.burn_rate.arn
}

output "burn_rate_alarm_name" { value = aws_cloudwatch_metric_alarm.burn_rate_fast.alarm_name }
