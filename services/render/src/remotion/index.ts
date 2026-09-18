import { registerRoot } from "remotion";
import { RemotionRoot } from "./Root.tsx";

// The bundler's entry point. Separate from Root.tsx so the root can be
// imported by anything else without the side effect of registering it.
registerRoot(RemotionRoot);
