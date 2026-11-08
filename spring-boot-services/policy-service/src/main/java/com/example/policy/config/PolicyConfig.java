package com.example.policy.config;

import com.example.policy.ingest.SemanticChunker;
import com.zaxxer.hikari.HikariConfig;
import com.zaxxer.hikari.HikariDataSource;
import javax.sql.DataSource;
import org.springframework.beans.factory.annotation.Qualifier;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.jdbc.core.simple.JdbcClient;
import org.springframework.scheduling.concurrent.ThreadPoolTaskExecutor;

@Configuration
public class PolicyConfig {

    /**
     * Wrapper type so this second JdbcClient never competes with the application's main one.
     * With policy.datasource.read.url set, retrieval queries go to that read replica;
     * otherwise they use the primary. A short query timeout keeps a slow vector query from piling up requests.
     */
    public record ReadJdbc(JdbcClient client) {
    }

    @Bean
    ReadJdbc readJdbc(DataSource primary,
                      @Value("${policy.datasource.read.url:}") String readUrl,
                      @Value("${policy.datasource.read.username:${spring.datasource.username}}") String user,
                      @Value("${policy.datasource.read.password:${spring.datasource.password}}") String password,
                      @Value("${policy.datasource.read.pool-size:10}") int poolSize,
                      @Value("${policy.retrieval.query-timeout-seconds:3}") int timeoutSeconds) {
        DataSource ds = primary;
        if (!readUrl.isBlank()) {
            HikariConfig hc = new HikariConfig();
            hc.setJdbcUrl(readUrl);
            hc.setUsername(user);
            hc.setPassword(password);
            hc.setMaximumPoolSize(poolSize);
            hc.setReadOnly(true);
            hc.setPoolName("policy-read-replica");
            hc.setConnectionInitSql("SET hnsw.ef_search = 100");
            ds = new HikariDataSource(hc);
        }
        JdbcTemplate template = new JdbcTemplate(ds);
        template.setQueryTimeout(timeoutSeconds);
        return new ReadJdbc(JdbcClient.create(template));
    }

    @Bean(name = "ingestionExecutor")
    ThreadPoolTaskExecutor ingestionExecutor() {
        ThreadPoolTaskExecutor ex = new ThreadPoolTaskExecutor();
        ex.setCorePoolSize(2);
        ex.setMaxPoolSize(2);
        ex.setQueueCapacity(50);
        ex.setThreadNamePrefix("ingest-");
        return ex;
    }

    @Bean(name = "retrievalExecutor")
    ThreadPoolTaskExecutor retrievalExecutor() {
        ThreadPoolTaskExecutor ex = new ThreadPoolTaskExecutor();
        int n = Math.max(4, Runtime.getRuntime().availableProcessors() * 2);
        ex.setCorePoolSize(n);
        ex.setMaxPoolSize(n);
        ex.setQueueCapacity(200);
        ex.setThreadNamePrefix("retrieve-");
        return ex;
    }

    @Bean
    SemanticChunker.Config chunkerConfig(@Value("${policy.chunking.max-tokens:220}") int max,
                                         @Value("${policy.chunking.min-tokens:60}") int min,
                                         @Value("${policy.chunking.overlap-tokens:45}") int overlap,
                                         @Value("${policy.chunking.semantic:true}") boolean semantic) {
        return new SemanticChunker.Config(max, min, overlap, semantic);
    }
}
