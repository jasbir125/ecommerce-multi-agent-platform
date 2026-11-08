from app.config import Settings
from app.llm import ModelFactory, build_model, model_name


def s(**kw):
    return Settings(_env_file=None, model_provider="bedrock", **kw)


def test_bedrock_uses_its_own_model_id_not_a_claude_api_name():
    cfg = s(bedrock_model_id="eu.anthropic.claude-sonnet-5-5-v1:0", model_supervisor="claude-sonnet-5-5")
    assert model_name(cfg, "supervisor") == "eu.anthropic.claude-sonnet-5-5-v1:0"


def test_bedrock_per_agent_override_accepts_a_bedrock_id():
    cfg = s(model_order_agent="anthropic.claude-haiku-5-5-v1:0")
    assert model_name(cfg, "order") == "anthropic.claude-haiku-5-5-v1:0"


def test_other_providers_ignore_bedrock_ids():
    cfg = Settings(_env_file=None, model_provider="openai", model_supervisor="anthropic.claude-haiku-5-5-v1:0")
    assert model_name(cfg, "supervisor") == cfg.openai_model


def test_builds_a_bedrock_model_with_the_guardrail_attached():
    cfg = s(bedrock_guardrail_id="gr-123", bedrock_guardrail_version="2", aws_region="eu-west-1")
    m = build_model(cfg, "bedrock", cfg.bedrock_model_id)
    assert m.region_name == "eu-west-1"
    assert m.guardrail_config["guardrailIdentifier"] == "gr-123"


def test_fallback_is_openai_only_when_a_key_exists():
    assert ModelFactory(s(openai_api_key=None)).fallback("order") is None
    assert ModelFactory(s(openai_api_key="k")).fallback("order") is not None
