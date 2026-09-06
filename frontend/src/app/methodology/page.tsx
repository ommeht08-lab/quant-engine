import type { Metadata } from "next";

import FlagshipResearchPrototype from "@/components/research/FlagshipResearchPrototype";
import { AAPL_RESEARCH_FIXTURE } from "@/lib/research-fixture";

export const metadata: Metadata = {
  title: "Methodology | Valuation Engine",
  description: "Methods and limitations for the point-in-time valuation research platform.",
};

export default function MethodologyPage() {
  return <FlagshipResearchPrototype researchCase={AAPL_RESEARCH_FIXTURE} view="methodology" />;
}
