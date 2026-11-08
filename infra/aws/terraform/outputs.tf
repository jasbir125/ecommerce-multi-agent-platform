output "url" {
  value       = "${var.certificate_arn == "" ? "http" : "https"}://${aws_lb.main.dns_name}"
  description = "Operations UI (and the APIs when expose_apis = true)."
}

output "ecr_registry" {
  value = "${data.aws_caller_identity.current.account_id}.dkr.ecr.${var.region}.amazonaws.com"
}

output "ecr_repositories" {
  value = { for k, v in aws_ecr_repository.svc : k => v.repository_url }
}

output "cluster" {
  value = aws_ecs_cluster.main.name
}

output "private_subnet_ids" {
  value = aws_subnet.private[*].id
}

output "services_security_group_id" {
  value = aws_security_group.services.id
}

output "db_init_task_family" {
  value = aws_ecs_task_definition.db_init.family
}

output "rds_endpoint" {
  value = aws_db_instance.postgres.address
}

output "kafka_bootstrap" {
  value = aws_msk_cluster.kafka.bootstrap_brokers
}

output "bedrock_guardrail_id" {
  value = var.enable_guardrail ? aws_bedrock_guardrail.agents[0].guardrail_id : null
}
