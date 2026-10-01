import { StockAnalysisShell } from "../../../components/stock-analysis-shell";

export default async function AnalyzePage({ params }: { params: Promise<{ symbol: string }> }) {
  const { symbol } = await params;
  return <StockAnalysisShell symbol={decodeURIComponent(symbol)} />;
}
