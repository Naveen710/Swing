import { Suspense } from "react";

import { NewsEvents } from "../../../components/research/news";

export const metadata = { title: "News & filings · Research · NSE Swing" };

export default function Page() {
  return <Suspense><NewsEvents /></Suspense>;
}
