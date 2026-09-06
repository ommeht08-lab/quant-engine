import type { Metadata } from "next";

import FlagshipResearchPrototype from "@/components/research/FlagshipResearchPrototype";
import { AAPL_RESEARCH_FIXTURE } from "@/lib/research-fixture";

export const metadata: Metadata = {
  title: "AAPL Evidence | Valuation Engine",
  description: "Data-quality gates and filing lineage for the AAPL point-in-time research case.",
};

export default function AaplEvidencePage() {
  return <FlagshipResearchPrototype researchCase={AAPL_RESEARCH_FIXTURE} view="evidence" />;
}
