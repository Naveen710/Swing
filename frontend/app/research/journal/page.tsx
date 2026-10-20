import { Suspense } from "react";

import { DecisionJournal } from "../../../components/research/journal";

export const metadata = { title: "Decision journal · Research · NSE Swing" };

export default function Page() {
  return <Suspense><DecisionJournal /></Suspense>;
}
