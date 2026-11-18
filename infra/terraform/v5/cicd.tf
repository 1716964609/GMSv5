# Reuse the existing account-level GitHub OIDC provider.

data "aws_iam_openid_connect_provider" "github" {
  url = "https://token.actions.githubusercontent.com"
}

locals {
  github_repository  = "1716964609@100065427/GMSv5@1430006129"
  github_environment = "production"

  deployment_object_arns = [
    for filename in [
      "gmsv5-linux-arm64.tar.gz",
      "gmsv5-linux-arm64.tar.gz.sha256",
      "manifest.json"
    ] : "${aws_s3_bucket.releases.arn}/releases/*/${filename}"
  ]
}

resource "aws_iam_role" "github_deploy" {
  name                 = "gmsv5-github-deploy-role"
  max_session_duration = 3600

  assume_role_policy = jsonencode({
    Version = "2012-10-17"

    Statement = [{
      Effect = "Allow"

      Principal = {
        Federated = data.aws_iam_openid_connect_provider.github.arn
      }

      Action = "sts:AssumeRoleWithWebIdentity"

      Condition = {
        StringEquals = {
          "token.actions.githubusercontent.com:aud" = "sts.amazonaws.com"
          "token.actions.githubusercontent.com:sub" = "repo:${local.github_repository}:environment:${local.github_environment}"
        }
      }
    }]
  })

  lifecycle {
    precondition {
      condition = contains(
        data.aws_iam_openid_connect_provider.github.client_id_list,
        "sts.amazonaws.com"
      )
      error_message = "Existing GitHub OIDC provider does not support the STS audience."
    }
  }

  tags = {
    Name    = "gmsv5-github-deploy-role"
    Project = "GMSv5"
    Purpose = "ProductionContinuousDeployment"
  }
}

resource "aws_iam_role_policy" "github_deploy" {
  name = "gmsv5-github-deploy-policy"
  role = aws_iam_role.github_deploy.id

  policy = jsonencode({
    Version = "2012-10-17"

    Statement = [
      {
        Sid      = "UploadNewReleaseObjects"
        Effect   = "Allow"
        Action   = ["s3:PutObject"]
        Resource = local.deployment_object_arns

        Condition = {
          StringEquals = {
            "s3:if-none-match" = "*"
          }
        }
      },
      {
        Sid      = "DenyUnconditionalReleaseWrites"
        Effect   = "Deny"
        Action   = ["s3:PutObject"]
        Resource = local.deployment_object_arns

        Condition = {
          StringNotEquals = {
            "s3:if-none-match" = "*"
          }
        }
      },
      {
        Sid      = "ReadReleaseObjects"
        Effect   = "Allow"
        Action   = ["s3:GetObject"]
        Resource = local.deployment_object_arns
      },
      {
        Sid    = "DenyReleaseDeletion"
        Effect = "Deny"
        Action = [
          "s3:DeleteObject",
          "s3:DeleteObjectVersion"
        ]
        Resource = "${aws_s3_bucket.releases.arn}/releases/*"
      },
      {
        Sid    = "SendDedicatedDeploymentCommand"
        Effect = "Allow"
        Action = ["ssm:SendCommand"]

        Resource = [
          aws_ssm_document.deploy.arn,
          aws_instance.v5.arn
        ]
      },
      {
        Sid      = "ReadDeploymentCommandResult"
        Effect   = "Allow"
        Action   = ["ssm:GetCommandInvocation"]
        Resource = "*"

        Condition = {
          StringEquals = {
            "aws:RequestedRegion" = "ap-northeast-1"
          }
        }
      }
    ]
  })
}

resource "aws_ssm_document" "deploy" {
  name            = "GMSv5-DeployRelease"
  document_type   = "Command"
  document_format = "JSON"
  target_type     = "/AWS::EC2::Instance"

  content = jsonencode({
    schemaVersion = "2.2"
    description   = "Deploy a verified GMS v5 release by immutable commit SHA"

    parameters = {
      ReleaseSha = {
        type              = "String"
        description       = "Full lowercase 40-character Git commit SHA"
        allowedPattern    = "^[0-9a-f]{40}$"
        interpolationType = "ENV_VAR"
      }
    }

    mainSteps = [
      {
        action = "aws:runShellScript"
        name   = "deployRelease"

        precondition = {
          StringEquals = ["platformType", "Linux"]
        }

        inputs = {
          timeoutSeconds = "1800"

          runCommand = [
            "set -eu",
            "test -n \"$SSM_ReleaseSha\"",
            "test -x /opt/gmsv5/deploy/deploy.sh",
            "exec /bin/bash /opt/gmsv5/deploy/deploy.sh \"$SSM_ReleaseSha\""
          ]
        }
      }
    ]
  })

  tags = {
    Name    = "GMSv5-DeployRelease"
    Project = "GMSv5"
  }
}

output "github_deploy_role_arn" {
  value = aws_iam_role.github_deploy.arn
}

output "deployment_ssm_document_name" {
  value = aws_ssm_document.deploy.name
}

output "deployment_ssm_document_version" {
  value = aws_ssm_document.deploy.document_version
}
