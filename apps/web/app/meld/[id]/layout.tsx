"use client";

import { use } from "react";
import { StudioShell } from "@/components/studio/StudioShell";

export default function MeldLayout({ params, children }: LayoutProps<"/meld/[id]">) {
  const { id } = use(params);
  return <StudioShell id={id}>{children}</StudioShell>;
}
