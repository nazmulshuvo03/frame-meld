"use client";

import { useEffect } from "react";
import { useRouter } from "next/navigation";
import { me } from "@/lib/api";

export default function Home() {
  const router = useRouter();

  useEffect(() => {
    me()
      .then(() => router.replace("/melds"))
      .catch(() => router.replace("/login"));
  }, [router]);

  return <main className="p-8 text-sm text-neutral-500">Loading…</main>;
}
