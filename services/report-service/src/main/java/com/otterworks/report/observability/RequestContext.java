package com.otterworks.report.observability;

import java.util.regex.Pattern;
import org.slf4j.MDC;

/** Request-ID header, MDC keys and validation shared by the observability filters. */
public final class RequestContext {

    public static final String REQUEST_ID_HEADER = "X-Request-ID";
    public static final String REQUEST_ID_MDC_KEY = "requestId";
    public static final String TRACE_ID_MDC_KEY = "traceId";
    public static final String SPAN_ID_MDC_KEY = "spanId";

    private static final Pattern VALID_REQUEST_ID = Pattern.compile("^[A-Za-z0-9._:-]{1,128}$");

    private RequestContext() {
    }

    public static boolean isValidRequestId(String requestId) {
        return requestId != null && VALID_REQUEST_ID.matcher(requestId).matches();
    }

    public static String currentRequestId() {
        return MDC.get(REQUEST_ID_MDC_KEY);
    }
}
