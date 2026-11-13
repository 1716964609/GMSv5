
resource "aws_iam_role_policy" "otlp_token_read" {
  name = "gmsv5-otlp-token-read"
  role = aws_iam_role.ec2.name

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid      = "ReadProductionOTLPToken"
        Effect   = "Allow"
        Action   = ["ssm:GetParameter"]
        Resource = "arn:aws:ssm:ap-northeast-1:${data.aws_caller_identity.current.account_id}:parameter/gmsv5/prod/observability/otlp-token"
      }
    ]
  })
}
