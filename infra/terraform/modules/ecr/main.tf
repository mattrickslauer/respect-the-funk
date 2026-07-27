# ECR: one repository, holding both image tags (api, worker).
#
# infra/README lists ECR under "comes along for the ride" — cents, not a design
# decision. The lifecycle policy is the part worth writing: without it, every build
# accumulates forever and the "storage is a rounding error" claim quietly stops
# being true.

variable "name_prefix" { type = string }
variable "tags" {
  type    = map(string)
  default = {}
}

resource "aws_ecr_repository" "this" {
  name = var.name_prefix
  # Immutable would be better hygiene, but the demo pushes :latest repeatedly and a
  # blocked push mid-hackathon is a worse failure than an overwritten tag.
  image_tag_mutability = "MUTABLE"

  image_scanning_configuration {
    scan_on_push = true
  }

  tags = var.tags
}

resource "aws_ecr_lifecycle_policy" "this" {
  repository = aws_ecr_repository.this.name

  policy = jsonencode({
    rules = [
      {
        rulePriority = 1
        description  = "Keep the last 10 images; older ones are only build history."
        selection = {
          tagStatus   = "any"
          countType   = "imageCountMoreThan"
          countNumber = 10
        }
        action = { type = "expire" }
      },
    ]
  })
}

output "repository_url" { value = aws_ecr_repository.this.repository_url }
output "repository_arn" { value = aws_ecr_repository.this.arn }
output "repository_name" { value = aws_ecr_repository.this.name }
