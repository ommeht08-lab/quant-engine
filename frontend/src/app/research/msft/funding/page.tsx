import type { Metadata } from "next";
import MsftSensitivityPrototype from "@/components/research/MsftSensitivityPrototype";

export const metadata: Metadata = {
  title: "MSFT Funding | Valuation Engine",
  description: "Cash, debt, and revolver mechanics, including X1's year-2 funding refusal.",
};

export default function MsftFundingPage() {
  return <MsftSensitivityPrototype view="funding" />;
}
