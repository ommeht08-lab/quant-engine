import type { Metadata } from "next";
import MsftSensitivityPrototype from "@/components/research/MsftSensitivityPrototype";

export const metadata: Metadata = {
  title: "MSFT Sensitivity Case | Valuation Engine",
  description: "Archived MSFT assumption-sensitivity experiment — illustrative, not investment advice.",
};

export default function MsftResearchCasePage() {
  return <MsftSensitivityPrototype view="overview" />;
}
