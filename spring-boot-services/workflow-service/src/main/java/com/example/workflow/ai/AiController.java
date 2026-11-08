package com.example.workflow.ai;

import jakarta.validation.Valid;
import jakarta.validation.constraints.NotBlank;
import jakarta.validation.constraints.NotNull;
import java.math.BigDecimal;
import java.util.Map;
import org.springframework.web.bind.annotation.*;

/** Demo endpoints for the Spring AI features. */
@RestController
@RequestMapping("/api/ai")
public class AiController {

    private final AiAdvisorService advisor;

    public AiController(AiAdvisorService advisor) {
        this.advisor = advisor;
    }

    public record ClassifyRequest(@NotBlank String message) {
    }

    public record RecommendRequest(@NotBlank String workflowId, @NotBlank String orderId, @NotNull BigDecimal orderAmount,
                                   boolean refundEligible, Map<String, Object> verifiedFacts) {
    }

    @PostMapping("/classify")
    public IntentClassification classify(@Valid @RequestBody ClassifyRequest request) {
        return advisor.classify(request.message());
    }

    @PostMapping("/recommend")
    public ResolutionDecision recommend(@Valid @RequestBody RecommendRequest r) {
        return advisor.recommend(r.workflowId(), r.orderId(), r.verifiedFacts() == null ? Map.of() : r.verifiedFacts(),
                r.orderAmount(), r.refundEligible());
    }
}
