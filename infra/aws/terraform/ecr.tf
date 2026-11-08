locals {
  spring_services = {
    auth-service         = { port = 8093, cpu = 512, memory = 1024 }
    order-service        = { port = 8081, cpu = 512, memory = 1024 }
    shipment-service     = { port = 8082, cpu = 512, memory = 1024 }
    payment-service      = { port = 8083, cpu = 512, memory = 1024 }
    inventory-service    = { port = 8084, cpu = 512, memory = 1024 }
    approval-service     = { port = 8085, cpu = 512, memory = 1024 }
    policy-service       = { port = 8086, cpu = 1024, memory = 2048 }
    audit-service        = { port = 8087, cpu = 512, memory = 1024 }
    workflow-service     = { port = 8088, cpu = 1024, memory = 2048 }
    notification-service = { port = 8089, cpu = 512, memory = 1024 }
  }
  python_services = {
    ecommerce-mcp-server = { port = 8090, cpu = 512, memory = 1024 }
    policy-mcp-server    = { port = 8091, cpu = 512, memory = 1024 }
    agent-host           = { port = 8092, cpu = 1024, memory = 2048 }
    streamlit-ui         = { port = 8501, cpu = 512, memory = 1024 }
  }
  all_services = merge(local.spring_services, local.python_services)
  repositories = toset(concat(keys(local.all_services), ["db-init"]))
}

resource "aws_ecr_repository" "svc" {
  for_each             = local.repositories
  name                 = "${var.name_prefix}/${each.key}"
  image_tag_mutability = "MUTABLE"
  force_delete         = !var.db_deletion_protection

  image_scanning_configuration { scan_on_push = true }
  encryption_configuration { encryption_type = "AES256" }
}

resource "aws_ecr_lifecycle_policy" "svc" {
  for_each   = aws_ecr_repository.svc
  repository = each.value.name
  policy = jsonencode({
    rules = [{
      rulePriority = 1
      description  = "Keep the last 20 images"
      selection    = { tagStatus = "any", countType = "imageCountMoreThan", countNumber = 20 }
      action       = { type = "expire" }
    }]
  })
}
