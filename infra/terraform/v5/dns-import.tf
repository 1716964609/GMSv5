import {
  to = aws_route53_zone.production
  id = "Z05363321CQMGUZ68L65V"
}

import {
  to = aws_route53_record.apex_ipv4
  id = "Z05363321CQMGUZ68L65V_sunlightjetrans.com_A"
}

import {
  to = aws_route53_record.apex_ipv6
  id = "Z05363321CQMGUZ68L65V_sunlightjetrans.com_AAAA"
}

import {
  to = aws_route53_record.origin_v5
  id = "Z05363321CQMGUZ68L65V_origin-v5.sunlightjetrans.com_AAAA"
}

import {
  to = aws_route53_record.legacy_origin
  id = "Z05363321CQMGUZ68L65V_origin.sunlightjetrans.com_AAAA"
}

import {
  to = aws_route53_record.acm_validation
  id = "Z05363321CQMGUZ68L65V__2156c9a4b4ef612b6f40dccf59088945.sunlightjetrans.com_CNAME"
}

import {
  to = aws_route53_record.edge_test
  id = "Z05363321CQMGUZ68L65V_edge-test.sunlightjetrans.com_CNAME"
}
