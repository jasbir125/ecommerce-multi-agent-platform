package com.example.common.security;

import com.example.common.PlatformProperties;
import jakarta.servlet.FilterChain;
import jakarta.servlet.ReadListener;
import jakarta.servlet.ServletException;
import jakarta.servlet.ServletInputStream;
import jakarta.servlet.http.HttpServletRequest;
import jakarta.servlet.http.HttpServletRequestWrapper;
import jakarta.servlet.http.HttpServletResponse;
import java.io.IOException;
import org.springframework.core.Ordered;
import org.springframework.core.annotation.Order;
import org.springframework.stereotype.Component;
import org.springframework.web.filter.OncePerRequestFilter;

/**
 * Basic edge hardening: security response headers, and a cap on request body size (413 when Content-Length is
 * over the limit; a body streamed without a length is cut off at the limit while it is read).
 */
@Component
@Order(Ordered.HIGHEST_PRECEDENCE)
public class HardeningFilter extends OncePerRequestFilter {

    private final long maxBytes;

    public HardeningFilter(PlatformProperties props) {
        this.maxBytes = props.maxBodyBytes();
    }

    @Override
    protected void doFilterInternal(HttpServletRequest req, HttpServletResponse res, FilterChain chain) throws ServletException, IOException {
        res.setHeader("X-Content-Type-Options", "nosniff");
        res.setHeader("X-Frame-Options", "DENY");
        res.setHeader("Cache-Control", "no-store");
        res.setHeader("Referrer-Policy", "no-referrer");
        if (req.getContentLengthLong() > maxBytes) {
            res.setStatus(HttpServletResponse.SC_REQUEST_ENTITY_TOO_LARGE);
            res.setContentType("application/problem+json");
            res.getWriter().write("{\"title\":\"Payload too large\",\"status\":413,\"code\":\"PAYLOAD_TOO_LARGE\"}");
            return;
        }
        chain.doFilter(new Limited(req, maxBytes), res);
    }

    private static final class Limited extends HttpServletRequestWrapper {
        private final long max;

        Limited(HttpServletRequest r, long max) {
            super(r);
            this.max = max;
        }

        @Override
        public ServletInputStream getInputStream() throws IOException {
            ServletInputStream in = super.getInputStream();
            return new ServletInputStream() {
                private long read;

                @Override
                public int read() throws IOException {
                    int b = in.read();
                    if (b >= 0 && ++read > max) {
                        throw new IOException("request body exceeds " + max + " bytes");
                    }
                    return b;
                }

                @Override
                public int read(byte[] buf, int off, int len) throws IOException {
                    int n = in.read(buf, off, len);
                    if (n > 0 && (read += n) > max) {
                        throw new IOException("request body exceeds " + max + " bytes");
                    }
                    return n;
                }

                @Override
                public boolean isFinished() {
                    return in.isFinished();
                }

                @Override
                public boolean isReady() {
                    return in.isReady();
                }

                @Override
                public void setReadListener(ReadListener l) {
                    in.setReadListener(l);
                }
            };
        }
    }
}
