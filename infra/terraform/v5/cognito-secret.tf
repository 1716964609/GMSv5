resource "aws_ssm_parameter" "cognito_client_secret" {
  name        = "/gmsv5/cognito/client-secret"
  description = "GMS v5 Cognito confidential app client secret"
  type        = "SecureString"
  tier        = "Standard"
  value       = aws_cognito_user_pool_client.v5.client_secret

  tags = {
    Name = "gmsv5-cognito-client-secret"
  }
}

resource "aws_iam_role_policy" "cognito_secret_read" {
  name = "gmsv5-cognito-secret-read"
  role = aws_iam_role.ec2.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect   = "Allow"
        Action   = ["ssm:GetParameter"]
        Resource = aws_ssm_parameter.cognito_client_secret.arn
      }
    ]
  })
}
