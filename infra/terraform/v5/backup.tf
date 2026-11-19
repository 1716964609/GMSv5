data "aws_iam_policy_document" "dlm_assume" {
  statement {
    actions = ["sts:AssumeRole"]

    principals {
      type        = "Service"
      identifiers = ["dlm.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "dlm" {
  name               = "gmsv5-dlm-role"
  assume_role_policy = data.aws_iam_policy_document.dlm_assume.json

  tags = {
    Name = "gmsv5-dlm-role"
  }
}

resource "aws_iam_role_policy_attachment" "dlm" {
  role       = aws_iam_role.dlm.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AWSDataLifecycleManagerServiceRole"
}

resource "aws_iam_role_policy" "dlm_delete_guard" {
  name = "gmsv5-dlm-delete-guard"
  role = aws_iam_role.dlm.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid      = "ProtectUnmanagedSnapshots"
        Effect   = "Deny"
        Action   = "ec2:DeleteSnapshot"
        Resource = "*"
        Condition = {
          StringNotEquals = {
            "ec2:ResourceTag/GMSBackupPolicy" = "daily-data"
          }
        }
      }
    ]
  })
}

resource "aws_dlm_lifecycle_policy" "data_backup" {
  description        = "GMS v5 daily SQLite data EBS backup"
  execution_role_arn = aws_iam_role.dlm.arn
  state              = "ENABLED"

  policy_details {
    policy_type    = "EBS_SNAPSHOT_MANAGEMENT"
    resource_types = ["VOLUME"]

    target_tags = {
      Name = aws_ebs_volume.data.tags["Name"]
    }

    schedule {
      name = "DailyBackup"

      create_rule {
        interval      = 24
        interval_unit = "HOURS"
        times         = ["19:00"]
      }

      retain_rule {
        count = 2
      }

      copy_tags = false

      tags_to_add = {
        Name            = "gmsv5-daily-data-backup"
        GMSBackupPolicy = "daily-data"
        ManagedBy       = "DLM"
      }
    }
  }

  tags = {
    Name = "gmsv5-data-backup-policy"
  }

  depends_on = [
    aws_iam_role_policy_attachment.dlm,
    aws_iam_role_policy.dlm_delete_guard
  ]
}

output "data_backup_policy_id" {
  value = aws_dlm_lifecycle_policy.data_backup.id
}
