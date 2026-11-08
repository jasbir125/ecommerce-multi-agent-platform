package com.example.policy;

import com.example.policy.PolicyRecords.IngestRequest;
import com.example.policy.ingest.IngestionJobService;
import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.time.LocalDate;
import java.util.HashMap;
import java.util.Map;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.boot.ApplicationArguments;
import org.springframework.boot.ApplicationRunner;
import org.springframework.core.io.Resource;
import org.springframework.core.io.support.PathMatchingResourcePatternResolver;
import org.springframework.jdbc.core.simple.JdbcClient;
import org.springframework.stereotype.Component;

/**
 * Loads the demo policy corpus (src/main/resources/policies/*.md) through the normal ingestion pipeline
 * on first start. Each file starts with a small "---" header (key, version, title, type, region, dates...).
 */
@Component
public class PolicySeeder implements ApplicationRunner {

    private static final Logger log = LoggerFactory.getLogger(PolicySeeder.class);

    private final IngestionJobService jobs;
    private final JdbcClient jdbc;
    private final boolean enabled;

    public PolicySeeder(IngestionJobService jobs, JdbcClient jdbc, @Value("${policy.seed-on-startup}") boolean enabled) {
        this.jobs = jobs;
        this.jdbc = jdbc;
        this.enabled = enabled;
    }

    @Override
    public void run(ApplicationArguments args) throws IOException {
        if (!enabled || jdbc.sql("SELECT count(*) FROM policy_document").query(Long.class).single() > 0) {
            return;
        }
        int ok = 0, failed = 0;
        for (Resource r : new PathMatchingResourcePatternResolver().getResources("classpath:policies/*.md")) {
            var job = jobs.runNow(parse(new String(r.getInputStream().readAllBytes(), StandardCharsets.UTF_8)));
            if ("FAILED".equals(job.status())) {
                failed++;
                log.error("Seeding {} failed: {}", r.getFilename(), job.error());
            } else {
                ok++;
            }
        }
        log.info("Seeded demo policies: {} ok, {} failed", ok, failed);
    }

    static IngestRequest parse(String file) {
        String[] parts = file.split("(?m)^---\\s*$", 3);
        Map<String, String> h = new HashMap<>();
        for (String line : parts[1].strip().split("\\R")) {
            int i = line.indexOf(':');
            if (i > 0) {
                h.put(line.substring(0, i).strip(), line.substring(i + 1).strip());
            }
        }
        return new IngestRequest(h.get("key"), h.get("version"), h.get("title"), parts[2].strip(), "text/markdown",
                h.get("region"), h.get("type"), h.get("tenant"), h.get("segment"), h.get("product_category"), null,
                h.containsKey("effective_from") ? LocalDate.parse(h.get("effective_from")) : null,
                h.containsKey("effective_to") ? LocalDate.parse(h.get("effective_to")) : null);
    }
}
