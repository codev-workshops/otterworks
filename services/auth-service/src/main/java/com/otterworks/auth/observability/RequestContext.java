package com.otterworks.auth.observability;

import java.util.regex.Pattern;
import org.slf4j.MDC;

/** Request-scoped correlation identifiers shared by logs, traces and outgoing calls. */
public final class RequestContext {

  public static final String REQUEST_ID_HEADER = "X-Request-ID";
  public static final String REQUEST_ID_MDC_KEY = "requestId";

  private static final Pattern VALID_REQUEST_ID = Pattern.compile("^[A-Za-z0-9._:-]{1,128}$");

  private RequestContext() {}

  public static boolean isValidRequestId(String requestId) {
    return requestId != null && VALID_REQUEST_ID.matcher(requestId).matches();
  }

  public static String currentRequestId() {
    return MDC.get(REQUEST_ID_MDC_KEY);
  }
}
