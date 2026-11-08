locals {
  namespace = "${var.name_prefix}.internal"
  url       = { for k, v in local.all_services : k => "http://${k}.${local.namespace}:${v.port}" }
  openai_on = nonsensitive(var.openai_api_key != "")

  secret = { for k, v in aws_secretsmanager_secret.app : k => v.arn }

  common_env = {
    PLATFORM_ENV                   = var.platform_env
    PLATFORM_FAULTS_ENABLED        = "false"
    LOG_FORMAT                     = "json" # Python services: one JSON object per line for CloudWatch (Spring ignores it)
    JAVA_TOOL_OPTIONS              = "-XX:MaxRAMPercentage=75"
    SPRING_DATASOURCE_URL          = "jdbc:postgresql://${aws_db_instance.postgres.address}:5432/ecommerce?sslmode=require"
    SPRING_DATASOURCE_USERNAME     = "ecommerce"
    SPRING_KAFKA_BOOTSTRAP_SERVERS = aws_msk_cluster.kafka.bootstrap_brokers
    KAFKA_BOOTSTRAP_SERVERS        = aws_msk_cluster.kafka.bootstrap_brokers
    REDIS_URL                      = "redis://${aws_elasticache_replication_group.redis.primary_endpoint_address}:6379"
    PLATFORM_JWT_JWKS_URL          = "${local.url["auth-service"]}/.well-known/jwks.json"
    PLATFORM_JWT_ISSUER            = "order-recovery-auth"
    PLATFORM_JWT_AUDIENCE          = "order-recovery-api"
    AUTH_JWKS_URL                  = "${local.url["auth-service"]}/.well-known/jwks.json"
    AUTH_ISSUER                    = "order-recovery-auth"
    AUTH_AUDIENCE                  = "order-recovery-api"
    AUTH_SERVICE_URL               = local.url["auth-service"]
    PLATFORM_AUDIT_SERVICE_URL     = local.url["audit-service"]
    ORDER_SERVICE_URL              = local.url["order-service"]
    SHIPMENT_SERVICE_URL           = local.url["shipment-service"]
    PAYMENT_SERVICE_URL            = local.url["payment-service"]
    INVENTORY_SERVICE_URL          = local.url["inventory-service"]
    APPROVAL_SERVICE_URL           = local.url["approval-service"]
    POLICY_SERVICE_URL             = local.url["policy-service"]
    AUDIT_SERVICE_URL              = local.url["audit-service"]
    WORKFLOW_SERVICE_URL           = local.url["workflow-service"]
    NOTIFICATION_SERVICE_URL       = local.url["notification-service"]
    ECOMMERCE_MCP_URL              = "${local.url["ecommerce-mcp-server"]}/mcp"
    POLICY_MCP_URL                 = "${local.url["policy-mcp-server"]}/mcp"
  }

  common_secrets = {
    PLATFORM_SERVICE_TOKEN     = local.secret["service-token"]
    MCP_SERVICE_TOKEN          = local.secret["service-token"]
    SPRING_DATASOURCE_PASSWORD = local.secret["db-password"]
  }

  extra_env = {
    policy-service   = { POLICY_EMBEDDING_PROVIDER = var.policy_embedding_provider }
    workflow-service = { MODEL_PROVIDER = "openai" } # Spring AI advisor has no Bedrock client yet (see docs)
    agent-host = merge({
      MODEL_PROVIDER            = "bedrock"
      AWS_REGION                = var.region
      BEDROCK_MODEL_ID          = var.bedrock_model_id
      BEDROCK_GUARDRAIL_VERSION = var.enable_guardrail ? aws_bedrock_guardrail_version.agents[0].version : "DRAFT"
      AGENT_KAFKA_ENABLED       = "true"
      CHECKPOINTER              = "postgres"
    }, var.enable_guardrail ? { BEDROCK_GUARDRAIL_ID = aws_bedrock_guardrail.agents[0].guardrail_id } : {})
    streamlit-ui = {
      UI_AUTH_MODE                    = "login"
      UI_SHOW_DEMO_CREDENTIALS        = "false"
      STREAMLIT_API_BASE_URL          = local.url["workflow-service"]
      STREAMLIT_APPROVAL_API_BASE_URL = local.url["approval-service"]
      AGENT_HOST_URL                  = local.url["agent-host"]
    }
  }

  extra_secrets = {
    auth-service     = { AUTH_PRIVATE_KEY_PEM = local.secret["auth-key"], AUTH_DEMO_PASSWORD = local.secret["demo-password"] }
    policy-service   = { OPENAI_API_KEY = local.secret["openai-api-key"] }
    workflow-service = { OPENAI_API_KEY = local.secret["openai-api-key"] }
    agent-host = merge({ DATABASE_URL = aws_secretsmanager_secret.database_url.arn },
    local.openai_on ? { OPENAI_API_KEY = local.secret["openai-api-key"] } : {})
  }

  health_path = merge(
    { for k, _ in local.spring_services : k => "/actuator/health" },
    { ecommerce-mcp-server = "/health", policy-mcp-server = "/health", agent-host = "/health", streamlit-ui = "/_stcore/health" }
  )

  # Reachable from outside through the load balancer. Everything else is private (service discovery only).
  public_paths = merge(
    { streamlit-ui = [] },
    var.expose_apis ? {
      auth-service     = ["/api/auth/*", "/.well-known/*"]
      workflow-service = ["/api/workflows*", "/api/ai/*"]
      approval-service = ["/api/approvals*"]
      agent-host       = ["/api/agent/*"]
    } : {}
  )
  rule_priority = { auth-service = 10, workflow-service = 20, approval-service = 30, agent-host = 40 }
}

resource "aws_ecs_cluster" "main" {
  name = local.name
  setting {
    name  = "containerInsights"
    value = "enabled"
  }
}

resource "aws_service_discovery_private_dns_namespace" "main" {
  name = local.namespace
  vpc  = aws_vpc.main.id
}

resource "aws_service_discovery_service" "svc" {
  for_each = local.all_services
  name     = each.key
  dns_config {
    namespace_id = aws_service_discovery_private_dns_namespace.main.id
    dns_records {
      type = "A"
      ttl  = 10
    }
    routing_policy = "MULTIVALUE"
  }
  health_check_custom_config { failure_threshold = 1 }
}

resource "aws_cloudwatch_log_group" "svc" {
  for_each          = local.all_services
  name              = "/ecs/${local.name}/${each.key}"
  retention_in_days = var.log_retention_days
}

resource "aws_ecs_task_definition" "svc" {
  for_each                 = local.all_services
  family                   = "${local.name}-${each.key}"
  requires_compatibilities = ["FARGATE"]
  network_mode             = "awsvpc"
  cpu                      = each.value.cpu
  memory                   = each.value.memory
  execution_role_arn       = aws_iam_role.execution.arn
  task_role_arn            = each.key == "agent-host" ? aws_iam_role.agent.arn : aws_iam_role.task.arn

  runtime_platform {
    operating_system_family = "LINUX"
    cpu_architecture        = "X86_64"
  }

  container_definitions = jsonencode([{
    name         = each.key
    image        = "${aws_ecr_repository.svc[each.key].repository_url}:${var.image_tag}"
    essential    = true
    portMappings = [{ containerPort = each.value.port, protocol = "tcp" }]
    environment  = [for k, v in merge(local.common_env, lookup(local.extra_env, each.key, {})) : { name = k, value = v }]
    secrets      = [for k, v in merge(local.common_secrets, lookup(local.extra_secrets, each.key, {})) : { name = k, valueFrom = v }]
    logConfiguration = {
      logDriver = "awslogs"
      options = {
        "awslogs-group"         = aws_cloudwatch_log_group.svc[each.key].name
        "awslogs-region"        = var.region
        "awslogs-stream-prefix" = "ecs"
      }
    }
  }])
}

resource "aws_ecs_service" "svc" {
  for_each        = local.all_services
  name            = each.key
  cluster         = aws_ecs_cluster.main.id
  task_definition = aws_ecs_task_definition.svc[each.key].arn
  desired_count   = var.desired_count
  launch_type     = "FARGATE"

  health_check_grace_period_seconds = contains(keys(local.public_paths), each.key) ? 180 : null

  network_configuration {
    subnets          = aws_subnet.private[*].id
    security_groups  = [aws_security_group.services.id]
    assign_public_ip = false
  }

  service_registries {
    registry_arn = aws_service_discovery_service.svc[each.key].arn
  }

  dynamic "load_balancer" {
    for_each = contains(keys(local.public_paths), each.key) ? [1] : []
    content {
      target_group_arn = aws_lb_target_group.svc[each.key].arn
      container_name   = each.key
      container_port   = each.value.port
    }
  }

  deployment_circuit_breaker {
    enable   = true
    rollback = true
  }

  depends_on = [aws_lb_listener.main]
}

# ------------------------------------------------------------------ load balancer
resource "aws_lb" "main" {
  name                       = substr(local.name, 0, 32)
  load_balancer_type         = "application"
  internal                   = false
  subnets                    = aws_subnet.public[*].id
  security_groups            = [aws_security_group.alb.id]
  drop_invalid_header_fields = true
  idle_timeout               = 300 # live runs stream for a while
}

resource "aws_lb_target_group" "svc" {
  for_each             = local.public_paths
  name                 = substr("${var.name_prefix}-${each.key}", 0, 32)
  port                 = local.all_services[each.key].port
  protocol             = "HTTP"
  target_type          = "ip"
  vpc_id               = aws_vpc.main.id
  deregistration_delay = 30
  health_check {
    path     = local.health_path[each.key]
    matcher  = "200"
    interval = 30
  }
}

resource "aws_lb_listener" "main" {
  load_balancer_arn = aws_lb.main.arn
  port              = var.certificate_arn == "" ? 80 : 443
  protocol          = var.certificate_arn == "" ? "HTTP" : "HTTPS"
  ssl_policy        = var.certificate_arn == "" ? null : "ELBSecurityPolicy-TLS13-1-2-2021-06"
  certificate_arn   = var.certificate_arn == "" ? null : var.certificate_arn

  default_action {
    type             = "forward"
    target_group_arn = aws_lb_target_group.svc["streamlit-ui"].arn
  }
}

resource "aws_lb_listener" "http_redirect" {
  count             = var.certificate_arn == "" ? 0 : 1
  load_balancer_arn = aws_lb.main.arn
  port              = 80
  protocol          = "HTTP"
  default_action {
    type = "redirect"
    redirect {
      port        = "443"
      protocol    = "HTTPS"
      status_code = "HTTP_301"
    }
  }
}

resource "aws_lb_listener_rule" "api" {
  for_each     = { for k, v in local.public_paths : k => v if k != "streamlit-ui" }
  listener_arn = aws_lb_listener.main.arn
  priority     = local.rule_priority[each.key]
  action {
    type             = "forward"
    target_group_arn = aws_lb_target_group.svc[each.key].arn
  }
  condition {
    path_pattern { values = each.value }
  }
}
