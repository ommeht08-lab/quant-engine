import type { Metadata } from "next";

import FlagshipResearchPrototype from "@/components/research/FlagshipResearchPrototype";
import { AAPL_RESEARCH_FIXTURE } from "@/lib/research-fixture";

export const metadata: Metadata = {
  title: "AAPL Valuation | Valuation Engine",
  description: "DCF scenarios and market comparison for the AAPL point-in-time research case.",
};

export default function AaplValuationPage() {
  return <FlagshipResearchPrototype researchCase={AAPL_RESEARCH_FIXTURE} view="valuation" />;
}
