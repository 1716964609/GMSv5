data "aws_caller_identity" "current" {}

resource "aws_s3_bucket" "releases" {
  bucket = "gmsv5-releases-${data.aws_caller_identity.current.account_id}-apne1"

  tags = {
    Name = "gmsv5-releases"
  }
}

resource "aws_s3_bucket_public_access_block" "releases" {
  bucket = aws_s3_bucket.releases.id

  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_iam_role_policy" "release_read" {
  name = "gmsv5-s3-release-read"
  role = aws_iam_role.ec2.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect   = "Allow"
      Action   = ["s3:GetObject"]
      Resource = "${aws_s3_bucket.releases.arn}/releases/*"
    }]
  })
}

output "release_bucket" {
  value = aws_s3_bucket.releases.id
}
