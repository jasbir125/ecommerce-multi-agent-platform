# One-off task that creates the schema (pgvector extension, tables, seed data) in RDS.
# Run it once after `terraform apply` and after pushing the db-init image: see ../deployment.md.
resource "aws_cloudwatch_log_group" "db_init" {
  name              = "/ecs/${local.name}/db-init"
  retention_in_days = var.log_retention_days
}

resource "aws_ecs_task_definition" "db_init" {
  family                   = "${local.name}-db-init"
  requires_compatibilities = ["FARGATE"]
  network_mode             = "awsvpc"
  cpu                      = 256
  memory                   = 512
  execution_role_arn       = aws_iam_role.execution.arn
  task_role_arn            = aws_iam_role.task.arn

  container_definitions = jsonencode([{
    name      = "db-init"
    image     = "${aws_ecr_repository.svc["db-init"].repository_url}:${var.image_tag}"
    essential = true
    environment = [
      { name = "PGHOST", value = aws_db_instance.postgres.address },
      { name = "PGUSER", value = "ecommerce" },
      { name = "PGDATABASE", value = "ecommerce" },
      { name = "PGSSLMODE", value = "require" },
      { name = "LOAD_SEED_DATA", value = "false" },
    ]
    secrets = [{ name = "PGPASSWORD", valueFrom = local.secret["db-password"] }]
    logConfiguration = {
      logDriver = "awslogs"
      options = {
        "awslogs-group"         = aws_cloudwatch_log_group.db_init.name
        "awslogs-region"        = var.region
        "awslogs-stream-prefix" = "ecs"
      }
    }
  }])
}
