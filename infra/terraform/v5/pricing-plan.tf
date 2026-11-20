# Existing CloudFront Free Plan subscription.
# The ingress distribution and WAF are managed externally.

resource "aws_pricingplanmanager_subscription" "production" {
  plan_family = "CloudFront"
  plan_tier   = "FREE"

  resource_arns = [
    "arn:aws:cloudfront::${data.aws_caller_identity.current.account_id}:distribution/E1EF0DWDYA2XH8",
    "arn:aws:wafv2:us-east-1:${data.aws_caller_identity.current.account_id}:global/webacl/CreatedByCloudFront-056855fc/70949ab2-45bd-432f-bd01-56e89676739a",
    aws_route53_zone.production.arn
  ]

  lifecycle {
    prevent_destroy = true
  }
}

import {
  to = aws_pricingplanmanager_subscription.production
  id = "arn:aws:pricingplanmanager::180294215932:subscription:sub_389BDNiLNPwkYONibzOjsYDgjLs"
}
