resource "random_password" "service_token" {
  length  = 64
  special = false
}

locals {
  secret_values = {
    service-token  = random_password.service_token.result
    db-password    = random_password.db.result
    openai-api-key = var.openai_api_key != "" ? var.openai_api_key : "not-set"
  }
}

# for_each needs keys that are not derived from sensitive values.
locals {
  secret_names = toset(["service-token", "db-password", "openai-api-key"])
}

resource "aws_secretsmanager_secret" "app" {
  for_each                = local.secret_names
  name                    = "${local.name}/${each.key}"
  recovery_window_in_days = 7
}

resource "aws_secretsmanager_secret_version" "app" {
  for_each      = local.secret_names
  secret_id     = aws_secretsmanager_secret.app[each.key].id
  secret_string = local.secret_values[each.key]
}

# The agent host reads one connection string, so the password is embedded here (still only in Secrets Manager).
resource "aws_secretsmanager_secret" "database_url" {
  name                    = "${local.name}/database-url"
  recovery_window_in_days = 7
}

resource "aws_secretsmanager_secret_version" "database_url" {
  secret_id     = aws_secretsmanager_secret.database_url.id
  secret_string = "postgresql://ecommerce:${random_password.db.result}@${aws_db_instance.postgres.address}:5432/ecommerce?sslmode=require"
}
