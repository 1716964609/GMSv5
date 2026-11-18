resource "aws_iam_role_policy" "github_deploy_lock" {
  name = "gmsv5-github-deploy-lock"
  role = aws_iam_role.github_deploy.id

  policy = jsonencode({
    Version = "2012-10-17"

    Statement = [
      {
        Sid      = "AcquireDeploymentLock"
        Effect   = "Allow"
        Action   = ["s3:PutObject"]
        Resource = "${aws_s3_bucket.releases.arn}/deployment-control/lock.json"

        Condition = {
          StringEquals = {
            "s3:if-none-match" = "*"
          }
        }
      },
      {
        Sid      = "ReadDeploymentLock"
        Effect   = "Allow"
        Action   = ["s3:GetObject"]
        Resource = "${aws_s3_bucket.releases.arn}/deployment-control/lock.json"
      },
      {
        Sid      = "ReleaseDeploymentLockConditionally"
        Effect   = "Allow"
        Action   = ["s3:DeleteObject"]
        Resource = "${aws_s3_bucket.releases.arn}/deployment-control/lock.json"

        Condition = {
          Null = {
            "s3:if-match" = "false"
          }

          StringNotEquals = {
            "s3:if-match" = "*"
          }
        }
      }
    ]
  })
}
