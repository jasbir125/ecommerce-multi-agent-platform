package com.example.audit;

import com.example.audit.AuditRecords.*;
import jakarta.validation.Valid;
import java.util.List;
import java.util.Map;
import org.springframework.http.HttpStatus;
import org.springframework.web.bind.annotation.*;

@RestController
@RequestMapping("/api/audit")
public class AuditController {

    private final AuditService service;

    public AuditController(AuditService service) {
        this.service = service;
    }

    @PostMapping("/events")
    @ResponseStatus(HttpStatus.CREATED)
    public Map<String, Object> append(@Valid @RequestBody AuditEventRequest request) {
        return Map.of("id", service.append(request));
    }

    @GetMapping("/workflows/{workflowId}")
    public List<AuditEvent> forWorkflow(@PathVariable String workflowId) {
        return service.forWorkflow(workflowId);
    }
}
