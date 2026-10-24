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

data "aws_subnet" "existing" {
  id = "subnet-0661039664bcdb978"
}

data "aws_ssm_parameter" "al2023" {
  name = "/aws/service/ami-amazon-linux-latest/al2023-ami-kernel-default-arm64"
}

resource "aws_security_group" "v5" {
  name        = "gmsv5-app-sg"
  description = "GMS v5 application"
  vpc_id      = data.aws_subnet.existing.vpc_id

  egress {
    from_port        = 0
    to_port          = 0
    protocol         = "-1"
    cidr_blocks      = ["0.0.0.0/0"]
    ipv6_cidr_blocks = ["::/0"]
  }

  tags = {
    Name = "gmsv5-app-sg"
  }
}

resource "aws_network_interface" "v5" {
  subnet_id          = data.aws_subnet.existing.id
  security_groups    = [aws_security_group.v5.id]
  ipv6_address_count = 1

  tags = {
    Name = "gmsv5-primary-eni"
  }
}

data "aws_iam_policy_document" "ec2_assume" {
  statement {
    actions = ["sts:AssumeRole"]

    principals {
      type        = "Service"
      identifiers = ["ec2.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "ec2" {
  name               = "gmsv5-ec2-role"
  assume_role_policy = data.aws_iam_policy_document.ec2_assume.json
}

resource "aws_iam_role_policy_attachment" "ssm" {
  role       = aws_iam_role.ec2.name
  policy_arn = "arn:aws:iam::aws:policy/AmazonSSMManagedInstanceCore"
}

resource "aws_iam_instance_profile" "ec2" {
  name = "gmsv5-ec2-instance-profile"
  role = aws_iam_role.ec2.name
}

resource "aws_instance" "v5" {
  ami                  = data.aws_ssm_parameter.al2023.value
  instance_type        = "t4g.small"
  availability_zone    = data.aws_subnet.existing.availability_zone
  iam_instance_profile = aws_iam_instance_profile.ec2.name

  user_data                   = file("${path.module}/ssm-bootstrap.sh")
  user_data_replace_on_change = true

  instance_market_options {
    market_type = "spot"

    spot_options {
      spot_instance_type             = "persistent"
      instance_interruption_behavior = "stop"
    }
  }

  network_interface {
    network_interface_id = aws_network_interface.v5.id
    device_index         = 0
  }

  root_block_device {
    volume_type           = "gp3"
    volume_size           = 16
    encrypted             = true
    delete_on_termination = true
  }

  tags = {
    Name = "gmsv5-app"
  }

  depends_on = [aws_iam_role_policy_attachment.ssm]

  lifecycle {
    ignore_changes = [ami]
  }
}

resource "aws_ebs_volume" "data" {
  availability_zone = data.aws_subnet.existing.availability_zone
  size              = 10
  type              = "gp3"
  encrypted         = true

  tags = {
    Name = "gmsv5-sqlite-data"
  }

  lifecycle {
    prevent_destroy = true
  }
}

resource "aws_volume_attachment" "data" {
  device_name = "/dev/sdf"
  volume_id   = aws_ebs_volume.data.id
  instance_id = aws_instance.v5.id
}

output "instance_id" {
  value = aws_instance.v5.id
}

output "data_volume_id" {
  value = aws_ebs_volume.data.id
}

output "ipv6_address" {
  value = aws_network_interface.v5.ipv6_addresses
}
