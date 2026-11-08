package com.example.workflow.ai;

/** Structured output for intent classification. */
public record IntentClassification(String intent, double confidence, String reason, String source) {
}
