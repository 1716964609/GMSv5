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
