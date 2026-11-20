locals {
  gmsv5_dns_domain = "sunlightjetrans.com"

  # Production ingress distribution is managed externally.
  gmsv5_ingress_cf_domain  = "d2yxdtkikux26e.cloudfront.net"
  gmsv5_ingress_cf_zone_id = "Z2FDTNDATAQYW2"
}

resource "aws_route53_zone" "production" {
  name          = local.gmsv5_dns_domain
  comment       = "GMS v5 production DNS"
  force_destroy = false

  lifecycle {
    prevent_destroy = true
  }
}

resource "aws_route53_record" "apex_ipv4" {
  zone_id = aws_route53_zone.production.zone_id
  name    = local.gmsv5_dns_domain
  type    = "A"

  alias {
    name                   = local.gmsv5_ingress_cf_domain
    zone_id                = local.gmsv5_ingress_cf_zone_id
    evaluate_target_health = false
  }
}

resource "aws_route53_record" "apex_ipv6" {
  zone_id = aws_route53_zone.production.zone_id
  name    = local.gmsv5_dns_domain
  type    = "AAAA"

  alias {
    name                   = local.gmsv5_ingress_cf_domain
    zone_id                = local.gmsv5_ingress_cf_zone_id
    evaluate_target_health = false
  }
}

resource "aws_route53_record" "origin_v5" {
  zone_id = aws_route53_zone.production.zone_id
  name    = "origin-v5.${local.gmsv5_dns_domain}"
  type    = "AAAA"
  ttl     = 60

  records = tolist(aws_network_interface.v5.ipv6_addresses)
}

# Legacy origin retained during DNS migration stabilization.
resource "aws_route53_record" "legacy_origin" {
  zone_id = aws_route53_zone.production.zone_id
  name    = "origin.${local.gmsv5_dns_domain}"
  type    = "AAAA"
  ttl     = 60

  records = [
    "2406:da14:190f:d6a8:6c4a:da24:5520:8a7e"
  ]
}

# Required for ACM managed certificate renewal.
resource "aws_route53_record" "acm_validation" {
  zone_id = aws_route53_zone.production.zone_id
  name    = "_2156c9a4b4ef612b6f40dccf59088945.${local.gmsv5_dns_domain}"
  type    = "CNAME"
  ttl     = 60

  records = [
    "_2ff2ffd03398c53edc3017577a9af281.jkddzztszm.acm-validations.aws."
  ]
}

# Temporary record retained for DNS migration comparison.
resource "aws_route53_record" "edge_test" {
  zone_id = aws_route53_zone.production.zone_id
  name    = "edge-test.${local.gmsv5_dns_domain}"
  type    = "CNAME"
  ttl     = 60

  records = [
    "${local.gmsv5_ingress_cf_domain}."
  ]
}
