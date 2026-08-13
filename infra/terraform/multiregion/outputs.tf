output "cluster_id" {
  value       = cockroach_cluster.residency.id
  description = "Cloud cluster UUID. This is what `ccloud cluster delete` and every teardown command take."
}

output "cluster_name" {
  value       = cockroach_cluster.residency.name
  description = "Cluster name."
}

output "sql_regions" {
  value       = [for r in cockroach_cluster.residency.regions : "aws-${r.name}"]
  description = <<-EOT
    The region names as SQL sees them, which is not how the Cloud API names them.
    `ALTER DATABASE ... ADD REGION` in migration 024 takes these — `aws-eu-west-1`, not
    `eu-west-1`. Emitted so the runbook can be copied rather than retyped, because the
    error from the short form is "region not found", which reads like the region failed
    to provision rather than like a spelling difference.
  EOT
}

output "region_hosts" {
  value = {
    for r in cockroach_cluster.residency.regions : r.name => r.sql_dns
  }
  description = "Per-region SQL hostname. Connecting through the EU host is how you demonstrate a read served locally rather than from Virginia."
}

output "connection_string" {
  value       = data.cockroach_connection_string.app.connection_string
  sensitive   = true
  description = "DATABASE_URL for platform/schema/apply.py. `terraform output -raw connection_string`."
}

output "teardown" {
  value       = "ccloud cluster delete ${cockroach_cluster.residency.name}   # or: terraform destroy"
  description = <<-EOT
    Printed on every apply, on purpose. Standard bills ~$0.18/hour for 2 vCPU whether or
    not anything is connected — about $4.30 a day against a $10 project budget. The
    cluster is ephemeral and forgetting this line is the single most expensive mistake
    available in this repository.
  EOT
}
