import type { Metadata } from "next";
import MsftSensitivityPrototype from "@/components/research/MsftSensitivityPrototype";

export const metadata: Metadata = {
  title: "MSFT Evidence | Valuation Engine",
  description: "Source archives, SHA-256 hashes, and the stored-fact cross-check behind this case.",
};

export default function MsftEvidencePage() {
  return <MsftSensitivityPrototype view="evidence" />;
}
