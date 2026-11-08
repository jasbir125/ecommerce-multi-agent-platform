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

    private final PlatformProperties props;

    public PlatformRestClient(PlatformProperties props) {
        this.props = props;
    }

    public RestClient forBaseUrl(String baseUrl) {
        var settings = ClientHttpRequestFactorySettings.DEFAULTS
                .withConnectTimeout(Duration.ofSeconds(2))
                .withReadTimeout(Duration.ofSeconds(5));
        return RestClient.builder()
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
                    return execution.execute(request, body);
                })
                .build();
    }
}
