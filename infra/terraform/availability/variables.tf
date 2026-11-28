# AWS region is the existing production region.
variable "aws_region" {
  type    = string
  default = "ap-northeast-1"
}

# Existing application EC2; NEVER replaced by this module.
variable "app_instance_id" {
  type    = string
  default = "i-0decf6bc205e273d9"
}

# Existing observability EC2; NEVER replaced by this module.
variable "observability_instance_id" {
  type    = string
  default = "i-021170e56849c597b"
}

# Public CloudFront-fronted application URL; no credentials involved.
variable "health_url" {
  type    = string
  default = "https://sunlightjetrans.com/"
}

# Existing public CloudFront relay; no distribution modification occurs.
variable "discord_cloudfront_url" {
  type    = string
  default = "https://d17p4dgtsvz5w4.cloudfront.net/notify"
}

# Name only; the token value is inserted separately, OUTSIDE Terraform.
variable "discord_token_parameter" {
  type    = string
  default = "/gmsv5/availability/discord-relay-token"
}

# Requested choice: PAY_PER_REQUEST; may cost money outside a strict free tier.
# PROVISIONED 1/1 fits a qualifying unused provisioned allowance better.
variable "dynamodb_billing_mode" {
  type    = string
  default = "PAY_PER_REQUEST"
  validation {
    condition     = contains(["PAY_PER_REQUEST", "PROVISIONED"], var.dynamodb_billing_mode)
    error_message = "Use PAY_PER_REQUEST or PROVISIONED."
  }
}

# Default false until IPv6 reachability and IAM provider are tested on Grafana.
variable "attach_grafana_read_policy" {
  type    = bool
  default = true
}

# IAM role already attached to the Observability EC2, from existing Terraform.
variable "observability_role_name" {
  type    = string
  default = "gmsv5-observability-role"
}

# Rollout gate. Do not rely on alarm actions: CloudWatch emits state-change
# events to EventBridge whether or not the alarm has actions.
#   off    - alarm evaluates; EventBridge rule DISABLED; Lambda not invoked.
#   shadow - rule ENABLED; Lambda records state and logs, sends nothing.
#   live   - rule ENABLED; Lambda sends Discord notifications.
variable "burn_rate_alarm_rollout" {
  type    = string
  default = "off"
  validation {
    condition     = contains(["off", "shadow", "live"], var.burn_rate_alarm_rollout)
    error_message = "Use off, shadow or live."
  }
}

# Rollout gate of the continuous failure alarm (continuous_failure.tf), with
# the same meaning as burn_rate_alarm_rollout. The two are independent.
variable "continuous_failure_alarm_rollout" {
  type    = string
  default = "off"
  validation {
    condition     = contains(["off", "shadow", "live"], var.continuous_failure_alarm_rollout)
    error_message = "Use off, shadow or live."
  }
}

# Discord user mentioned on HIGH alarm messages (ALARM and DATA INSUFFICIENT
# of both alarms). Set ONLY in terraform.tfvars, which is not in Git (*.tfvars
# in .gitignore). Empty = no mention; the Lambda then sends HIGH messages
# without a mention and logs a warning. A precondition on the Lambda refuses a
# plan with an alarm rollout at "live" and no ID.
variable "discord_alert_user_id" {
  type      = string
  default   = ""
  sensitive = true
  validation {
    condition     = can(regex("^([0-9]{17,20})?$", var.discord_alert_user_id))
    error_message = "Use an empty string or a 17-20 digit Discord user ID."
  }
}
