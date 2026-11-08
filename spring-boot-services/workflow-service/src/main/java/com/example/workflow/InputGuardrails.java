package com.example.workflow;

import com.example.common.web.ApiException;
import java.util.List;
import java.util.regex.Pattern;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.http.HttpStatus;
import org.springframework.stereotype.Component;

/** First guardrail layer: reject unsafe customer messages before any agent sees them. */
@Component
public class InputGuardrails {

    private static final List<Rule> RULES = List.of(
            new Rule("BYPASS_APPROVAL", Pattern.compile("(skip|bypass|without|no)\\s+(the\\s+)?(manager\\s+)?approval|auto-?approve", Pattern.CASE_INSENSITIVE)),
            new Rule("PROMPT_INJECTION", Pattern.compile("ignore (all )?(the )?(previous|prior|above) instructions|system prompt|you are now", Pattern.CASE_INSENSITIVE)),
            new Rule("DESTRUCTIVE_COMMAND", Pattern.compile("drop\\s+table|delete\\s+from|rm\\s+-rf|;\\s*shutdown", Pattern.CASE_INSENSITIVE)),
            new Rule("OTHER_CUSTOMER_DATA", Pattern.compile("(another|other|someone else'?s?)\\s+customer'?s?\\s+(order|data|account)", Pattern.CASE_INSENSITIVE)));

    private record Rule(String code, Pattern pattern) {
    }

    private final int maxLength;

    public InputGuardrails(@Value("${workflow.max-message-length}") int maxLength) {
        this.maxLength = maxLength;
    }

    /** @return the code of the first rule violated, or throws; callers audit the exception. */
    public void check(String message) {
        if (message.length() > maxLength) {
            throw new ApiException(HttpStatus.UNPROCESSABLE_ENTITY, "INPUT_TOO_LONG", "Message exceeds " + maxLength + " characters");
        }
        for (Rule r : RULES) {
            if (r.pattern().matcher(message).find()) {
                throw new ApiException(HttpStatus.UNPROCESSABLE_ENTITY, "INPUT_REJECTED_" + r.code(),
                        "Request rejected by input guardrails (" + r.code() + ")");
            }
        }
    }
}
