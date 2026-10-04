import type { Metadata } from "next";
import MsftSensitivityPrototype from "@/components/research/MsftSensitivityPrototype";

export const metadata: Metadata = {
  title: "MSFT Scenarios | Valuation Workspace",
  description: "Per-scenario unlevered FCF, sensitivity vs. R, and predeclared financing invariants.",
};

export default function MsftScenariosPage() {
  return <MsftSensitivityPrototype view="scenarios" />;
}
