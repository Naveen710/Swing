import { CompanyDossier } from "../../../../components/research/dossier";

export default async function Page({ params }: { params: Promise<{ symbol: string }> }) {
  const { symbol } = await params;
  return <CompanyDossier symbol={decodeURIComponent(symbol).toUpperCase()} />;
}

export async function generateMetadata({ params }: { params: Promise<{ symbol: string }> }) {
  const { symbol } = await params;
  return { title: `${decodeURIComponent(symbol).toUpperCase()} dossier · Research · NSE Swing` };
}
