"use client";

import { ScriptStage } from "@/components/studio/ScriptStage";
import { useReadyStudio } from "@/components/studio/StudioShell";

export default function ScriptPage() {
  const { studio, script } = useReadyStudio();
  return <ScriptStage studio={studio} script={script} />;
}
