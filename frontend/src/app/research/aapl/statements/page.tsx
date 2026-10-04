import type { Metadata } from "next";

import FlagshipResearchPrototype from "@/components/research/FlagshipResearchPrototype";
import { AAPL_RESEARCH_FIXTURE } from "@/lib/research-fixture";

export const metadata: Metadata = {
  title: "AAPL Historical Statements | Valuation Workspace",
  description: "Auditable point-in-time historical statements for the AAPL research case.",
};

export default function AaplStatementsPage() {
  return <FlagshipResearchPrototype researchCase={AAPL_RESEARCH_FIXTURE} view="statements" />;
}
