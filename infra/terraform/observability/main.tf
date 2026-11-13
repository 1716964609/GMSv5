terraform {
  required_version = ">= 1.16.0"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 6.0"
    }
  }
}

provider "aws" {
  region = "ap-northeast-1"
}

# Reuse existing GMS v5 subnet.
data "aws_subnet" "existing" {
  id = "subnet-0661039664bcdb978"
}

# Amazon Linux 2023 ARM64.
data "aws_ssm_parameter" "al2023" {
  name = "/aws/service/ami-amazon-linux-latest/al2023-ami-kernel-default-arm64"
}

# -----------------------------------------
# Network / Security Group
# -----------------------------------------

resource "aws_security_group" "observability" {
  name_prefix = "gmsv5-observability-"
  description = "Observability infrastructure"
  vpc_id      = data.aws_subnet.existing.vpc_id

  # No inbound access initially.
  # Allow IPv6 outbound for SSM and updates.
  egress {
    from_port        = 0
    to_port          = 0
    protocol         = "-1"
    ipv6_cidr_blocks = ["::/0"]
  }

  tags = {
    Name    = "gmsv5-observability-sg"
    Project = "GMSv5"
  }
}

# -----------------------------------------
# IAM / Systems Manager
# -----------------------------------------

data "aws_iam_policy_document" "assume_ec2" {
  statement {
    actions = ["sts:AssumeRole"]

    principals {
      type        = "Service"
      identifiers = ["ec2.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "observability" {
  name               = "gmsv5-observability-role"
  assume_role_policy = data.aws_iam_policy_document.assume_ec2.json
}

resource "aws_iam_role_policy_attachment" "ssm" {
  role       = aws_iam_role.observability.name
  policy_arn = "arn:aws:iam::aws:policy/AmazonSSMManagedInstanceCore"
}

resource "aws_iam_instance_profile" "observability" {
  name = "gmsv5-observability-profile"
  role = aws_iam_role.observability.name
}

# -----------------------------------------
# Observability EC2
# -----------------------------------------

resource "aws_instance" "observability" {
  ami           = data.aws_ssm_parameter.al2023.value
  instance_type = "t4g.small"

  subnet_id                   = data.aws_subnet.existing.id
  vpc_security_group_ids      = [aws_security_group.observability.id]
  associate_public_ip_address = false
  ipv6_address_count          = 1

  iam_instance_profile = aws_iam_instance_profile.observability.name

  user_data = file("${path.module}/user-data.sh")

  instance_market_options {
    market_type = "spot"

    spot_options {
      spot_instance_type             = "persistent"
      instance_interruption_behavior = "stop"
    }
  }

  # Avoid surplus CPU-credit charges.
  credit_specification {
    cpu_credits = "standard"
  }

  # Root EBS only.
  root_block_device {
    volume_type           = "gp3"
    volume_size           = 10
    encrypted             = true
    delete_on_termination = true
  }

  metadata_options {
    http_endpoint = "enabled"
    http_tokens   = "required"
  }

  monitoring = false

  tags = {
    Name        = "gmsv5-observability"
    Project     = "GMSv5"
    Environment = "operations"
    Role        = "observability"
    ManagedBy   = "terraform"
  }

  depends_on = [
    aws_iam_role_policy_attachment.ssm
  ]

  lifecycle {
    ignore_changes  = [ami]
    prevent_destroy = true
  }
}

# -----------------------------------------
# Outputs
# -----------------------------------------

output "instance_id" {
  value = aws_instance.observability.id
}

output "ipv6_addresses" {
  value = aws_instance.observability.ipv6_addresses
}

output "root_volume_id" {
  value = aws_instance.observability.root_block_device[0].volume_id
}

output "security_group_id" {
  value = aws_security_group.observability.id
}

# OTLP/HTTPS from GMS v5 Production EC2 only.
resource "aws_vpc_security_group_ingress_rule" "otlp_from_production" {
  security_group_id = aws_security_group.observability.id

  description = "OTLP HTTPS from GMSv5 Production"
  ip_protocol = "tcp"
  from_port   = 8443
  to_port     = 8443

  cidr_ipv6 = "2406:da14:190f:d655:3b6:c3da:aeb4:17c7/128"
}
