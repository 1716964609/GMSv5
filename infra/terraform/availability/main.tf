# GMS v5 - independent AWS availability monitoring module.
# No existing CloudFront distribution, EC2 instance or SG is managed here.
terraform {
  # Match the existing repository's Terraform version policy.
  required_version = ">= 1.16.0"
  # AWS manages DynamoDB, Lambda, EventBridge, CloudWatch, IAM and SSM IAM references.
  required_providers {
    aws     = { source = "hashicorp/aws", version = "~> 6.0" }
    archive = { source = "hashicorp/archive", version = "~> 2.0" }
  }
}

# Use the region where the current EC2 instances exist.
provider "aws" { region = var.aws_region }

# Resolve account ID only to construct the minimum-scoped SSM ARN.
data "aws_caller_identity" "current" {}

# Package Python code as a deterministic Lambda deployment artifact.
data "archive_file" "lambda" {
  type        = "zip"
  source_dir  = "${path.module}/lambda"
  excludes    = ["__pycache__"]
  output_path = "${path.module}/lambda-package.zip"
}

# DynamoDB uses one PK (instance_id) and sort key (event_time).
# Actual events use ISO UTC timestamps with an event-ID suffix.
# The reserved ~STATE row stores small per-instance reconciliation state.
resource "aws_dynamodb_table" "events" {
  name         = "gmsv5-availability-events"
  billing_mode = var.dynamodb_billing_mode
  # Provisioned 1/1 can use an eligible existing AWS free-tier allowance.
  read_capacity  = var.dynamodb_billing_mode == "PROVISIONED" ? 1 : null
  write_capacity = var.dynamodb_billing_mode == "PROVISIONED" ? 1 : null
  # Primary key: EC2 instance identifier.
  hash_key = "instance_id"
  # Sort key: UTC event timestamp (or reserved ~STATE).
  range_key = "event_time"
  attribute {
    name = "instance_id"
    type = "S"
  }
  attribute {
    name = "event_time"
    type = "S"
  }
  # Old incident events expire after 400 days; state rows never expire.
  ttl {
    attribute_name = "expires_at"
    enabled        = true
  }
  # This table contains operational evidence; don't accidentally destroy it.
  lifecycle { prevent_destroy = true }
  tags = { Project = "GMSv5", Owner = "SRE" }
}

# Trust policy permits only the Lambda service to use this execution role.
data "aws_iam_policy_document" "lambda_assume" {
  statement {
    effect  = "Allow"
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["lambda.amazonaws.com"]
    }
  }
}

# Dedicated least-privilege execution role for the availability function.
resource "aws_iam_role" "lambda" {
  name               = "gmsv5-availability-lambda-role"
  assume_role_policy = data.aws_iam_policy_document.lambda_assume.json
}

# IAM policy limits writes to the availability table and metrics namespace.
data "aws_iam_policy_document" "lambda_runtime" {
  statement {
    sid       = "AvailabilityTable"
    actions   = ["dynamodb:GetItem", "dynamodb:PutItem", "dynamodb:UpdateItem", "dynamodb:Query"]
    resources = [aws_dynamodb_table.events.arn]
  }
  # PutMetricData has no standard resource ARN, so restrict namespace instead.
  statement {
    sid       = "MetricsOnlyInAvailabilityNamespace"
    actions   = ["cloudwatch:PutMetricData"]
    resources = ["*"]
    condition {
      test     = "StringEquals"
      variable = "cloudwatch:namespace"
      values   = ["GMS/Availability"]
    }
  }
  # Burn rate notifications read the last 60 minutes of HTTPUp (read only).
  # GetMetricData has no resource ARN for classic metrics (only the optional
  # "dataset" type) and cloudwatch:namespace does not apply to it, so the
  # Resource must be "*"; the metric is fixed in burn_rate_context.py.
  statement {
    sid       = "ReadBurnRateContextMetric"
    actions   = ["cloudwatch:GetMetricData"]
    resources = ["*"]
    condition {
      test     = "StringEquals"
      variable = "aws:RequestedRegion"
      values   = [var.aws_region]
    }
  }
  # Only the one manually created SecureString is readable.
  statement {
    sid     = "ReadRelaySecret"
    actions = ["ssm:GetParameter"]
    resources = [
      "arn:aws:ssm:${var.aws_region}:${data.aws_caller_identity.current.account_id}:parameter${var.discord_token_parameter}"
    ]
  }
  # Read-only EC2 and Spot request status for interruption evidence.
  # These Describe actions do not support resource-level permissions.
  # No EC2 mutations are allowed.
  statement {
    sid       = "ReadEC2State"
    actions   = ["ec2:DescribeInstances", "ec2:DescribeSpotInstanceRequests"]
    resources = ["*"]
  }
  # CloudWatch Logs group is created separately; Lambda writes streams/events.
  statement {
    sid       = "WriteFunctionLogs"
    actions   = ["logs:CreateLogStream", "logs:PutLogEvents"]
    resources = ["${aws_cloudwatch_log_group.lambda.arn}:*"]
  }
}

# Attach policy to the dedicated Lambda execution role.
resource "aws_iam_role_policy" "lambda_runtime" {
  name   = "gmsv5-availability-runtime"
  role   = aws_iam_role.lambda.id
  policy = data.aws_iam_policy_document.lambda_runtime.json
}

# Pre-create a log group and cap log retention to limit storage cost.
resource "aws_cloudwatch_log_group" "lambda" {
  name              = "/aws/lambda/gmsv5-availability"
  retention_in_days = 14
  tags              = { Project = "GMSv5" }
}

# Lambda uses AWS-managed networking, NOT the IPv6-only application VPC.
# It can reach the public CloudFront frontend and existing Discord distribution.
resource "aws_lambda_function" "availability" {
  function_name    = "gmsv5-availability"
  role             = aws_iam_role.lambda.arn
  runtime          = "python3.12"
  handler          = "handler.handler"
  filename         = data.archive_file.lambda.output_path
  source_code_hash = data.archive_file.lambda.output_base64sha256
  memory_size      = 128
  timeout          = 90
  # A short poll follows EC2 running events; one-minute probes are fallback.
  environment {
    variables = {
      TABLE_NAME              = aws_dynamodb_table.events.name
      APP_INSTANCE_ID         = var.app_instance_id
      OBS_INSTANCE_ID         = var.observability_instance_id
      HEALTH_URL              = var.health_url
      DISCORD_CLOUDFRONT_URL  = var.discord_cloudfront_url
      DISCORD_TOKEN_PARAMETER = var.discord_token_parameter
      # Spot interruption reason evidence (lambda/spot_status.py); fails open.
      SPOT_STATUS_COLLECTION = "enabled"
      # Burn rate alarm notifications (burn_rate.tf, lambda/burn_rate.py).
      BURN_RATE_ALARM_NAME = aws_cloudwatch_metric_alarm.burn_rate_fast.alarm_name
      BURN_RATE_ALARM_ARN  = aws_cloudwatch_metric_alarm.burn_rate_fast.arn
      BURN_RATE_DELIVERY   = var.burn_rate_alarm_rollout == "live" ? "enabled" : "disabled"

      # Continuous failure alarm notifications (continuous_failure.tf). Name and
      # ARN are built from known values, so the plan shows the other variables
      # unchanged; a test checks the ARN format against the alarm.
      CONTINUOUS_FAILURE_ALARM_NAME = local.continuous_failure_alarm_name
      CONTINUOUS_FAILURE_ALARM_ARN  = local.continuous_failure_alarm_arn
      CONTINUOUS_FAILURE_DELIVERY   = var.continuous_failure_alarm_rollout == "live" ? "enabled" : "disabled"
    }
  }
  depends_on = [aws_iam_role_policy.lambda_runtime, aws_cloudwatch_log_group.lambda]
  tags       = { Project = "GMSv5", Owner = "SRE" }
}

# Rule 1: stopping/stopped/running transitions for the two existing instances.
# stopping is evidence only; incidents still start at stopped.
resource "aws_cloudwatch_event_rule" "state" {
  name = "gmsv5-availability-instance-state"
  event_pattern = jsonencode({
    source        = ["aws.ec2"]
    "detail-type" = ["EC2 Instance State-change Notification"]
    detail = {
      "instance-id" = [var.app_instance_id, var.observability_instance_id]
      state         = ["stopping", "stopped", "running"]
    }
  })
}

# Target 1: deliver EC2 lifecycle events to the Lambda function.
resource "aws_cloudwatch_event_target" "state" {
  rule = aws_cloudwatch_event_rule.state.name
  arn  = aws_lambda_function.availability.arn
}

# Authorize the state-change rule to invoke the availability Lambda.
resource "aws_lambda_permission" "state" {
  statement_id  = "AllowStateChangeEvents"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.availability.function_name
  principal     = "events.amazonaws.com"
  source_arn    = aws_cloudwatch_event_rule.state.arn
}

# Rule 2: warnings and rebalance recommendations for the same instances.
# Interruption and rebalance event delivery from EC2 is BEST EFFORT.
resource "aws_cloudwatch_event_rule" "spot" {
  name = "gmsv5-availability-spot-signals"
  event_pattern = jsonencode({
    source = ["aws.ec2"]
    "detail-type" = [
      "EC2 Spot Instance Interruption Warning",
      "EC2 Instance Rebalance Recommendation"
    ]
    detail = { "instance-id" = [var.app_instance_id, var.observability_instance_id] }
  })
}

# Target 2: warnings trigger the same Lambda but a different code branch.
resource "aws_cloudwatch_event_target" "spot" {
  rule = aws_cloudwatch_event_rule.spot.name
  arn  = aws_lambda_function.availability.arn
}

# Allow the Spot-warning rule to invoke only the availability function.
resource "aws_lambda_permission" "spot" {
  statement_id  = "AllowSpotEvents"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.availability.function_name
  principal     = "events.amazonaws.com"
  source_arn    = aws_cloudwatch_event_rule.spot.arn
}

# Rule 3: one-minute public-site HTTP sample independent of EC2 events.
# This is necessary because EC2 state events do not measure HTTP availability.
resource "aws_cloudwatch_event_rule" "http_probe" {
  name                = "gmsv5-availability-http-probe"
  schedule_expression = "rate(1 minute)"
}

# Target 3: make public HTTP checks even when BOTH EC2 hosts are unavailable.
resource "aws_cloudwatch_event_target" "http_probe" {
  rule = aws_cloudwatch_event_rule.http_probe.name
  arn  = aws_lambda_function.availability.arn
}

# Allow the scheduled EventBridge rule to invoke the availability Lambda.
resource "aws_lambda_permission" "http_probe" {
  statement_id  = "AllowScheduledHTTPProbe"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.availability.function_name
  principal     = "events.amazonaws.com"
  source_arn    = aws_cloudwatch_event_rule.http_probe.arn
}

# Optional: Existing Grafana EC2 role may read availability metrics.
# Enable ONLY after verifying CloudWatch API reachability from IPv6-only host.
resource "aws_iam_role_policy" "grafana_cloudwatch_read" {
  count = var.attach_grafana_read_policy ? 1 : 0
  name  = "gmsv5-availability-cloudwatch-read"
  role  = var.observability_role_name
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect   = "Allow"
      Action   = ["cloudwatch:ListMetrics", "cloudwatch:GetMetricData"]
      Resource = "*"
    }]
  })
}

# Outputs are resource identifiers only; no secret values.
output "table_name" { value = aws_dynamodb_table.events.name }
output "lambda_name" { value = aws_lambda_function.availability.function_name }
output "cloudwatch_namespace" { value = "GMS/Availability" }
