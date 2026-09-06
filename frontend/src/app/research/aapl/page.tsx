import type { Metadata } from "next";

import FlagshipResearchPrototype from "@/components/research/FlagshipResearchPrototype";
import { AAPL_RESEARCH_FIXTURE } from "@/lib/research-fixture";

export const metadata: Metadata = {
  title: "AAPL Research Case | Valuation Engine",
  description: "Fixture-based interface prototype for an auditable point-in-time valuation case.",
};

export default function AaplResearchCasePage() {
  return <FlagshipResearchPrototype researchCase={AAPL_RESEARCH_FIXTURE} />;
}
