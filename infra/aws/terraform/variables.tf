variable "region" {
  type        = string
  default     = "ap-south-1"
  description = "AWS region. Pick one where your Bedrock model is available."
}

variable "environment" {
  type    = string
  default = "prod"
}

variable "name_prefix" {
  type    = string
  default = "recovery"
}

variable "az_count" {
  type    = number
  default = 2
}

variable "vpc_cidr" {
  type    = string
  default = "10.40.0.0/16"
}

variable "single_nat_gateway" {
  type        = bool
  default     = true
  description = "One NAT gateway (cheaper) or one per AZ (resilient)."
}

# ---------------------------------------------------------------- images
variable "image_tag" {
  type        = string
  default     = "latest"
  description = "Tag of the images pushed with ../push-images.sh."
}

# ---------------------------------------------------------------- edge
variable "allowed_ingress_cidrs" {
  type        = list(string)
  default     = ["0.0.0.0/0"]
  description = "Who may reach the load balancer. Restrict this to your office/VPN range."
}

variable "certificate_arn" {
  type        = string
  default     = ""
  description = "ACM certificate for HTTPS. Empty = plain HTTP on port 80 (demo only)."
}

# ---------------------------------------------------------------- sizing
variable "desired_count" {
  type    = number
  default = 1
}

variable "db_instance_class" {
  type    = string
  default = "db.t4g.medium"
}

variable "db_multi_az" {
  type    = bool
  default = false
}

variable "db_deletion_protection" {
  type    = bool
  default = true
}

variable "redis_node_type" {
  type    = string
  default = "cache.t4g.small"
}

variable "msk_instance_type" {
  type    = string
  default = "kafka.t3.small"
}

# ---------------------------------------------------------------- models
variable "bedrock_model_id" {
  type        = string
  default     = "anthropic.claude-3-5-sonnet-20241022-v2:0"
  description = "Foundation model (or inference-profile id) used by the agent host."
}

variable "bedrock_model_arns" {
  type        = list(string)
  default     = ["arn:aws:bedrock:*::foundation-model/*", "arn:aws:bedrock:*:*:inference-profile/*"]
  description = "Models the task role may invoke. Narrow this to the model you use."
}

variable "enable_guardrail" {
  type    = bool
  default = true
}

variable "openai_api_key" {
  type        = string
  default     = ""
  sensitive   = true
  description = "Only needed for OpenAI embeddings (policy-service) and the workflow-service advisor. Leave empty to use the offline hash embedder."
}

variable "policy_embedding_provider" {
  type        = string
  default     = "hash"
  description = "openai (needs openai_api_key) or hash (offline, lexical). Existing vectors are 1536-d OpenAI embeddings: choose openai for production quality."
}

variable "log_retention_days" {
  type    = number
  default = 30
}

variable "expose_apis" {
  type        = bool
  default     = false
  description = "Also publish the workflow, approval and agent-host APIs on the load balancer (for Postman/CI). Off = only the UI is public."
}
