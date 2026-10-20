import { Suspense } from "react";

import { EvidenceViewer } from "../../../../components/research/evidence";

export const metadata = { title: "Evidence · Research · NSE Swing" };

export default async function Page({ params }: { params: Promise<{ ref: string }> }) {
  const { ref } = await params;
  return <Suspense><EvidenceViewer refId={decodeURIComponent(ref)} /></Suspense>;
}
