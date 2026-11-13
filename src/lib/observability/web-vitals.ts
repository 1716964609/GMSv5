import { onCLS, onFCP, onINP, onLCP, onTTFB, type Metric } from "web-vitals";

import { isRumActive, reportWebVital } from "./rum-client.ts";

let started = false;

/**
 * Report Core Web Vitals (LCP, INP, CLS) plus FCP / TTFB once per page load.
 * Values are reported when final (page hidden), matching web-vitals defaults.
 */
export function startWebVitalsReporting(): void {
  if (started || !isRumActive()) {
    return;
  }
  started = true;
  try {
    const report = (metric: Metric) => reportWebVital(metric.name, metric.value, metric.rating);
    onLCP(report);
    onINP(report);
    onCLS(report);
    onFCP(report);
    onTTFB(report);
  } catch {
    // RUM must never break the page.
  }
}
