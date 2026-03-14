import { StockDetailShell } from "../../../components/stock-detail-shell";

export default async function StockDetailPage({
  params
}: {
  params: Promise<{ symbol: string }>;
}) {
  const { symbol } = await params;
  return <StockDetailShell symbol={symbol} />;
}

