import { notFound } from "next/navigation";

import { SegmentPage } from "../../../../components/research/segments";
import { isSegment } from "../../../../lib/research";

const TITLES = { large: "Large cap (NIFTY 100)", mid: "Mid cap (NIFTY Midcap 150)", small: "Small cap (NIFTY Smallcap 250)" };

export default async function Page({ params }: { params: Promise<{ seg: string }> }) {
  const { seg } = await params;
  if (!isSegment(seg)) notFound();
  return <SegmentPage seg={seg} />;
}

export async function generateMetadata({ params }: { params: Promise<{ seg: string }> }) {
  const { seg } = await params;
  return { title: `${isSegment(seg) ? TITLES[seg] : "Segment"} · Research · NSE Swing` };
}
