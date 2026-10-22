"use client";

import { FilmStage } from "@/components/studio/FilmStage";
import { useReadyStudio } from "@/components/studio/StudioShell";

export default function FilmPage() {
  const { studio, film, shots } = useReadyStudio();
  return <FilmStage studio={studio} film={film} shots={shots} />;
}
