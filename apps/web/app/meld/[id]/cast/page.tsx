"use client";

import { CastStage } from "@/components/studio/CastStage";
import { useReadyStudio } from "@/components/studio/StudioShell";

export default function CastPage() {
  const { studio, cast } = useReadyStudio();
  return <CastStage studio={studio} cast={cast} />;
}
