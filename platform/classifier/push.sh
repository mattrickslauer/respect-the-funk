#!/usr/bin/env bash
# Build, validate, and push the classifier image to ECR. Prints the digest URI.
#
#     ./push.sh
#
# Then set that URI in platform/infra/terraform.tfvars as classifier_image_uri and
# apply. The digest form is used rather than a :tag on purpose — Lambda resolves a tag
# once at create time and never again, so re-pushing :latest leaves the function
# silently running the old image while every console and plan reports it as current.
set -euo pipefail
here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$here"

engine="${CONTAINER_ENGINE:-podman}"
region="${AWS_REGION:-us-east-1}"
repo="$(cd ../infra && terraform output -raw classifier_repository_url)"
registry="${repo%%/*}"

# Validation is not optional and is not a separate step somebody can forget. §3a of
# docs/2026-08-10-masters-and-classification.md is a genre classifier that was
# confidently wrong about every track and had nothing to say so.
echo "== validating before push =="
./tests/validate.sh

echo "== pushing to $repo =="
aws ecr get-login-password --region "$region" \
  | "$engine" login --username AWS --password-stdin "$registry"

"$engine" tag rtf-classifier:validate "$repo:latest"
"$engine" push "$repo:latest"

digest="$(aws ecr describe-images --region "$region" \
  --repository-name "$(basename "$repo")" \
  --image-ids imageTag=latest \
  --query 'imageDetails[0].imageDigest' --output text)"

echo
echo "pushed. Set this in platform/infra/terraform.tfvars:"
echo
echo "    classifier_image_uri = \"$repo@$digest\""
echo
echo "then: cd ../infra && terraform apply"
