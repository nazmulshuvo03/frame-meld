import "@testing-library/jest-dom/vitest";
import { cleanup } from "@testing-library/react";
import { afterEach } from "vitest";

// React Testing Library only auto-cleans when a global `afterEach` exists in
// the framework it detects. Doing it explicitly means a leaked component
// from one test cannot keep running timers into the next.
afterEach(cleanup);

// jsdom implements no IntersectionObserver, and the studio page uses one to
// tell which stage the reader is in. A stub that observes nothing is the
// right shape for these tests: the scroll position of a jsdom document is
// not a thing worth asserting on, and the nav's `aria-current` is the only
// thing downstream of it.
class NoopIntersectionObserver {
  observe(): void {}
  unobserve(): void {}
  disconnect(): void {}
  takeRecords(): [] {
    return [];
  }
  readonly root = null;
  readonly rootMargin = "";
  readonly thresholds: readonly number[] = [];
}

Object.defineProperty(globalThis, "IntersectionObserver", {
  writable: true,
  configurable: true,
  value: NoopIntersectionObserver,
});
