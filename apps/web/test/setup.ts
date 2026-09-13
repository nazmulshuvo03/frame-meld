import "@testing-library/jest-dom/vitest";
import { cleanup } from "@testing-library/react";
import { afterEach } from "vitest";

// React Testing Library only auto-cleans when a global `afterEach` exists in
// the framework it detects. Doing it explicitly means a leaked component
// from one test cannot keep running timers into the next.
afterEach(cleanup);
