# The label-scope architecture, composed.
#
# This is infra/architecture.pdf as HCL: Lambda + SQS + Batch(Fargate Spot) + SSM,
# with B2 holding every byte. Five services, no database tier, no CDN, no VPC of our
# own — see infra/README for why each of those is absent rather than forgotten.
#
# Realistic idle cost: under $1/month, and none of it compute.

terraform {
  required_version = ">= 1.6"
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
  }

  # Local state by default so a first `apply` needs nothing but credentials.
  # Uncomment for anything shared — two people applying against local state is how
  # infrastructure gets destroyed by accident.
  #
  # backend "s3" {
  #   bucket = "remixkit-tfstate"
  #   key    = "prod/terraform.tfstate"
  #   region = "us-east-1"
  # }
}

provider "aws" {
  region = var.region
}

locals {
  name_prefix = "remixkit-${var.env}"
  tags = {
    Project   = "remixkit"
    Env       = var.env
    Tenant    = var.tenant_id
    ManagedBy = "terraform"
  }
}

# The default VPC, on purpose. A VPC of our own would want a NAT gateway for the
# worker to reach B2 and the providers, and a NAT is ~$32/month of pure idle floor —
# on its own, more than the rest of this architecture costs combined.
data "aws_vpc" "default" {
  default = true
}

data "aws_subnets" "default" {
  filter {
    name   = "vpc-id"
    values = [data.aws_vpc.default.id]
  }
}

resource "aws_security_group" "worker" {
  name        = "${local.name_prefix}-worker"
  description = "Generator container: egress only."
  vpc_id      = data.aws_vpc.default.id

  # No ingress rule at all. Nothing connects *to* the worker — it pulls from SQS and
  # pushes to B2 and the providers.
  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }

  tags = local.tags
}

# ---------------------------------------------------------------- modules
module "ecr" {
  source      = "../../modules/ecr"
  name_prefix = local.name_prefix
  tags        = local.tags
}

module "queue" {
  source      = "../../modules/queue"
  name_prefix = local.name_prefix
  tags        = local.tags
}

module "secrets" {
  source      = "../../modules/secrets"
  name_prefix = "remixkit"
  env         = var.env
  tags        = local.tags
}

module "api" {
  source      = "../../modules/api"
  name_prefix = local.name_prefix
  env         = var.env
  image_uri   = "${module.ecr.repository_url}:${var.api_image_tag}"
  queue_url   = module.queue.queue_url
  queue_arn   = module.queue.queue_arn
  ssm_path    = module.secrets.parameter_path
  b2_bucket   = var.b2_bucket
  tenant_id   = var.tenant_id
  tags        = local.tags
}

module "worker" {
  source             = "../../modules/worker"
  name_prefix        = local.name_prefix
  env                = var.env
  image_uri          = "${module.ecr.repository_url}:${var.worker_image_tag}"
  queue_url          = module.queue.queue_url
  queue_arn          = module.queue.queue_arn
  ssm_path           = module.secrets.parameter_path
  b2_bucket          = var.b2_bucket
  tenant_id          = var.tenant_id
  subnet_ids         = data.aws_subnets.default.ids
  security_group_ids = [aws_security_group.worker.id]
  tags               = local.tags
}
