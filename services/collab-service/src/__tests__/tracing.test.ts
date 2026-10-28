import { tracingEnabled } from '../tracing';

describe('tracingEnabled', () => {
  it('is off unless OTEL_SDK_DISABLED is exactly false', () => {
    expect(tracingEnabled({})).toBe(false);
    expect(tracingEnabled({ OTEL_SDK_DISABLED: 'true' })).toBe(false);
    expect(tracingEnabled({ OTEL_SDK_DISABLED: ' FALSE ' })).toBe(true);
  });
});
