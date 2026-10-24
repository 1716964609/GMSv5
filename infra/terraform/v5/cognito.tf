resource "aws_cognito_user_pool" "v5" {
  name           = "gmsv5-users"
  user_pool_tier = "LITE"

  username_attributes      = ["email"]
  auto_verified_attributes = ["email"]

  mfa_configuration   = "OFF"
  deletion_protection = "ACTIVE"

  admin_create_user_config {
    allow_admin_create_user_only = false
  }

  password_policy {
    minimum_length    = 12
    require_lowercase = true
    require_uppercase = true
    require_numbers   = true
    require_symbols   = true
  }

  account_recovery_setting {
    recovery_mechanism {
      name     = "verified_email"
      priority = 1
    }
  }

  tags = {
    Name = "gmsv5-users"
  }
}

resource "aws_cognito_user_pool_client" "v5" {
  name         = "gmsv5-web"
  user_pool_id = aws_cognito_user_pool.v5.id

  generate_secret = true

  allowed_oauth_flows_user_pool_client = true
  allowed_oauth_flows                  = ["code"]
  allowed_oauth_scopes                 = ["openid", "email", "profile"]

  supported_identity_providers = ["COGNITO"]

  callback_urls = [
    "http://localhost:3100/api/auth/callback"
  ]

  logout_urls = [
    "http://localhost:3100/"
  ]

  explicit_auth_flows = [
    "ALLOW_USER_PASSWORD_AUTH",
    "ALLOW_REFRESH_TOKEN_AUTH"
  ]

  prevent_user_existence_errors = "ENABLED"
  enable_token_revocation       = true
}

resource "aws_cognito_user_pool_domain" "v5" {
  domain                = "gmsv5-${data.aws_caller_identity.current.account_id}-apne1"
  user_pool_id          = aws_cognito_user_pool.v5.id
  managed_login_version = 1
}

output "cognito_user_pool_id" {
  value = aws_cognito_user_pool.v5.id
}

output "cognito_client_id" {
  value = aws_cognito_user_pool_client.v5.id
}

output "cognito_client_secret" {
  value     = aws_cognito_user_pool_client.v5.client_secret
  sensitive = true
}

output "cognito_domain" {
  value = "https://${aws_cognito_user_pool_domain.v5.domain}.auth.ap-northeast-1.amazoncognito.com"
}

output "cognito_issuer" {
  value = "https://cognito-idp.ap-northeast-1.amazonaws.com/${aws_cognito_user_pool.v5.id}"
}
