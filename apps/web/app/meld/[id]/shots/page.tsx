"use client";

import { ShotsStage } from "@/components/studio/ShotsStage";
import { useReadyStudio } from "@/components/studio/StudioShell";

export default function ShotsPage() {
  const { studio, shots } = useReadyStudio();
  return <ShotsStage studio={studio} shots={shots} />;
}
