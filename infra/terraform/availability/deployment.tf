resource "aws_iam_role_policy" "deployment_read" {
  name = "gmsv5-deployment-result-read"
  role = aws_iam_role.lambda.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect   = "Allow"
      Action   = ["ssm:GetCommandInvocation"]
      Resource = "*"

      Condition = {
        StringEquals = {
          "aws:RequestedRegion" = var.aws_region
        }
      }
    }]
  })
}

resource "aws_cloudwatch_event_rule" "deployment" {
  name = "gmsv5-deployment-status"

  event_pattern = jsonencode({
    source        = ["aws.ssm"]
    "detail-type" = ["EC2 Command Invocation Status-change Notification"]

    detail = {
      "document-name" = ["GMSv5-DeployRelease"]
      "instance-id"   = [var.app_instance_id]
      status          = ["InProgress", "Success", "Failed", "TimedOut", "Canceled"]
    }
  })
}

resource "aws_lambda_permission" "deployment" {
  statement_id  = "AllowDeploymentStatusEvents"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.availability.function_name
  principal     = "events.amazonaws.com"
  source_arn    = aws_cloudwatch_event_rule.deployment.arn
}

resource "aws_cloudwatch_event_target" "deployment" {
  rule = aws_cloudwatch_event_rule.deployment.name
  arn  = aws_lambda_function.availability.arn

  depends_on = [
    aws_lambda_permission.deployment,
    aws_iam_role_policy.deployment_read
  ]
}
