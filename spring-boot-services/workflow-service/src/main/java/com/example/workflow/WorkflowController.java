package com.example.workflow;

import com.example.workflow.WorkflowService.*;
import jakarta.validation.Valid;
import java.util.List;
import java.util.Map;
import org.springframework.http.HttpStatus;
import org.springframework.web.bind.annotation.*;

@RestController
@RequestMapping("/api/workflows")
public class WorkflowController {

    private final WorkflowService service;

    public WorkflowController(WorkflowService service) {
        this.service = service;
    }

    @PostMapping
    @ResponseStatus(HttpStatus.CREATED)
    public Workflow create(@Valid @RequestBody CreateRequest request) {
        return service.create(request);
    }

    @GetMapping
    public List<Workflow> list(@RequestParam(required = false) String status) {
        return service.list(status);
    }

    @GetMapping("/{workflowId}")
    public Workflow get(@PathVariable String workflowId) {
        return service.get(workflowId);
    }

    @GetMapping("/events/recent")
    public List<Map<String, Object>> recentEvents(@RequestParam(defaultValue = "50") int limit, @RequestParam(required = false) String topic) {
        return service.recentEvents(limit, topic);
    }

    @PostMapping("/{workflowId}/resume")
    public Workflow resume(@PathVariable String workflowId, @Valid @RequestBody ResumeRequest request) {
        return service.resume(workflowId, request);
    }

    @GetMapping("/{workflowId}/trace")
    public Map<String, Object> trace(@PathVariable String workflowId) {
        return service.trace(workflowId);
    }
}
