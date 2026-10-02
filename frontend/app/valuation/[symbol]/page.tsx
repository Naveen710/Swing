import { ValuationShell } from "../../../components/valuation-shell";

export default async function ValuationPage({ params }: { params: Promise<{ symbol: string }> }) {
  const { symbol } = await params;
  return <ValuationShell symbol={decodeURIComponent(symbol)} />;
}
