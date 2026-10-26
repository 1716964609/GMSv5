resource "aws_cloudfront_key_value_store" "deepl_proxy" {
  name = "gmsv5-deepl-proxy-auth"
}

resource "aws_cloudfront_function" "deepl_proxy_auth" {
  name    = "gmsv5-deepl-proxy-auth"
  runtime = "cloudfront-js-2.0"
  publish = true

  code = file("${path.module}/deepl-proxy-auth.js")

  key_value_store_associations = [
    aws_cloudfront_key_value_store.deepl_proxy.arn
  ]
}

resource "aws_cloudfront_distribution" "deepl_proxy" {
  enabled         = true
  is_ipv6_enabled = true
  comment         = "GMS v5 DeepL API proxy"
  price_class     = "PriceClass_200"

  origin {
    domain_name = "api-free.deepl.com"
    origin_id   = "deepl-api-free"

    custom_origin_config {
      http_port              = 80
      https_port             = 443
      origin_protocol_policy = "https-only"
      origin_ssl_protocols   = ["TLSv1.2"]
    }
  }

  default_cache_behavior {
    target_origin_id       = "deepl-api-free"
    viewer_protocol_policy = "https-only"

    allowed_methods = [
      "GET", "HEAD", "OPTIONS",
      "PUT", "POST", "PATCH", "DELETE"
    ]

    cached_methods = ["GET", "HEAD"]

    # Managed-CachingDisabled
    cache_policy_id = "4135ea2d-6df8-44a3-9df3-4b5a84be39ad"

    # Managed-AllViewerExceptHostHeader
    origin_request_policy_id = "b689b0a8-53d0-40ab-baf2-68738e2966ac"

    function_association {
      event_type   = "viewer-request"
      function_arn = aws_cloudfront_function.deepl_proxy_auth.arn
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
    Purpose = "DeepLProxy"
  }
}

output "deepl_proxy_url" {
  value = "https://${aws_cloudfront_distribution.deepl_proxy.domain_name}"
}
