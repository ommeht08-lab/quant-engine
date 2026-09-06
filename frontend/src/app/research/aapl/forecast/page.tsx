import type { Metadata } from "next";

import FlagshipResearchPrototype from "@/components/research/FlagshipResearchPrototype";
import { AAPL_RESEARCH_FIXTURE } from "@/lib/research-fixture";

export const metadata: Metadata = {
  title: "AAPL Forecast | Valuation Engine",
  description: "Five-year operating forecast for the AAPL point-in-time research case.",
};

export default function AaplForecastPage() {
  return <FlagshipResearchPrototype researchCase={AAPL_RESEARCH_FIXTURE} view="forecast" />;
}
