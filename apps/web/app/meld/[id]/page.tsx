import { redirect } from "next/navigation";

/** A Meld opens on its first page. Each stage has its own URL, so a refresh
 *  stays where you were; this is only where a bare `/meld/:id` is sent. */
export default async function MeldIndex({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  redirect(`/meld/${id}/story`);
}
