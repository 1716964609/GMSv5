terraform {
  required_version = ">= 1.6.0"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 6.0"
    }
  }
}

provider "aws" {
  region = "ap-northeast-1"
}

resource "aws_cloudfront_key_value_store" "discord" {
  name = "gmsv5-discord-alert-kvs"
}

resource "aws_cloudfront_function" "discord" {
  name    = "gmsv5-discord-alert-auth"
  runtime = "cloudfront-js-2.0"
  publish = true
  code    = file("${path.module}/auth.js")

  key_value_store_associations = [
    aws_cloudfront_key_value_store.discord.arn
  ]
}

resource "aws_cloudfront_origin_request_policy" "discord" {
  name = "gmsv5-discord-alert-json"

  cookies_config {
    cookie_behavior = "none"
  }

  query_strings_config {
    query_string_behavior = "none"
  }

  headers_config {
    header_behavior = "whitelist"

    headers {
      items = ["Content-Type"]
    }
  }
}

resource "aws_cloudfront_distribution" "discord" {
  enabled         = true
  is_ipv6_enabled = true
  comment         = "GMS v5 Discord alert egress"
  price_class     = "PriceClass_200"

  origin {
    domain_name = "discord.com"
    origin_id   = "discord-webhook"

    custom_origin_config {
      http_port              = 80
      https_port             = 443
      origin_protocol_policy = "https-only"
      origin_ssl_protocols   = ["TLSv1.2"]
    }
  }

  default_cache_behavior {
    target_origin_id       = "discord-webhook"
    viewer_protocol_policy = "https-only"

    allowed_methods = [
      "GET", "HEAD", "OPTIONS",
      "PUT", "POST", "PATCH", "DELETE"
    ]

    cached_methods = ["GET", "HEAD"]

    cache_policy_id = "4135ea2d-6df8-44a3-9df3-4b5a84be39ad"

    origin_request_policy_id = (
      aws_cloudfront_origin_request_policy.discord.id
    )

    function_association {
      event_type   = "viewer-request"
      function_arn = aws_cloudfront_function.discord.arn
    }
  }

  restrictions {
    geo_restriction {
      restriction_type = "none"
    }
  }

  viewer_certificate {
    cloudfront_default_certificate = true
  }

  tags = {
    Project = "GMSv5"
    Purpose = "DiscordAlertEgress"
  }
}

output "discord_cloudfront_domain" {
  value = aws_cloudfront_distribution.discord.domain_name
}

output "discord_kvs_arn" {
  value = aws_cloudfront_key_value_store.discord.arn
}
