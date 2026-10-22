"use client";

import { StoryStage } from "@/components/studio/StoryStage";
import { useReadyStudio } from "@/components/studio/StudioShell";

export default function StoryPage() {
  const { studio, meld } = useReadyStudio();
  return (
    <StoryStage
      meldId={studio.id}
      meld={meld}
      // The shell outlives this page, so what the editor saved has to land in
      // its copy of the Meld too -- or coming back to Story from another
      // stage would show the text as it was before this visit.
      onSaved={(story) => studio.setMeld((current) => (current ? { ...current, story } : current))}
    />
  );
}
