import { redirect } from "next/navigation";

/** The five stages are one scrolling page now.
 *
 *  Kept as a redirect rather than deleted: these URLs were the app's whole
 *  navigation for six milestones, so they are in browser histories and
 *  bookmarks, and a 404 is a worse answer than the section they asked for.
 */
export default async function CastRedirect({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  redirect(`/meld/${id}#cast`);
}
