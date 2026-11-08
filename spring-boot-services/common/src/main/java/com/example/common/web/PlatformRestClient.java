package com.example.common.web;

import com.example.common.PlatformProperties;
import com.example.common.security.Caller;
import com.example.common.security.CallerFilter;
import com.example.common.security.CallerHolder;
import java.time.Duration;
import org.springframework.boot.web.client.ClientHttpRequestFactories;
import org.springframework.boot.web.client.ClientHttpRequestFactorySettings;
import org.springframework.stereotype.Component;
import org.springframework.web.client.RestClient;

/**
 * Builds RestClients for calling other platform services. Every outgoing call
 * carries the service token, the end customer's id and the correlation id, and
 * has connect/read timeouts so a slow dependency cannot hang a request.
 */
@Component
public class PlatformRestClient {

    private static final org.slf4j.Logger log = org.slf4j.LoggerFactory.getLogger("http.client");

    private final PlatformProperties props;
    private final org.springframework.web.client.RestClient.Builder builder;

    /** The auto-configured builder carries Micrometer observation, so trace context flows to the called service. */
    public PlatformRestClient(PlatformProperties props, org.springframework.web.client.RestClient.Builder builder) {
        this.props = props;
        this.builder = builder;
    }

    public RestClient forBaseUrl(String baseUrl) {
        var settings = ClientHttpRequestFactorySettings.DEFAULTS
                .withConnectTimeout(Duration.ofSeconds(2))
                .withReadTimeout(Duration.ofSeconds(5));
        return builder.clone()
                .baseUrl(baseUrl)
                .requestFactory(ClientHttpRequestFactories.get(settings))
                .requestInterceptor((request, body, execution) -> {
                    Caller c = CallerHolder.current();
                    request.getHeaders().set(CallerFilter.H_TOKEN, props.serviceToken());
                    if (c.customerId() != null) {
                        request.getHeaders().set(CallerFilter.H_CUSTOMER, c.customerId());
                    }
                    if (c.correlationId() != null) {
                        request.getHeaders().set(CallerFilter.H_CORRELATION, c.correlationId());
                    }
                    if (!c.roles().isEmpty()) {
                        request.getHeaders().set(CallerFilter.H_ROLES, String.join(",", c.roles()));
                    }
                    long t0 = System.nanoTime();
                    String target = request.getMethod() + " " + request.getURI().getHost() + ":" + request.getURI().getPort() + request.getURI().getPath();
                    try {
                        var response = execution.execute(request, body);
                        log.info("=> {}  {} {}ms corr={} req={}", target, response.getStatusCode().value(), (System.nanoTime() - t0) / 1_000_000,
                                c.correlationId(), com.example.common.util.LogSanitizer.clean(new String(body, java.nio.charset.StandardCharsets.UTF_8), 800));
                        return response;
                    } catch (java.io.IOException e) {
                        log.warn("=> {}  FAILED {}ms corr={} error={}", target, (System.nanoTime() - t0) / 1_000_000, c.correlationId(), e.toString());
                        throw e;
                    }
                })
                .build();
    }
}
