import type { Metadata } from "next";
import MsftSensitivityPrototype from "@/components/research/MsftSensitivityPrototype";

export const metadata: Metadata = {
  title: "MSFT Statements | Valuation Workspace",
  description: "Linked three-statement forecast, year by year, for the selected archived scenario.",
};

export default function MsftStatementsPage() {
  return <MsftSensitivityPrototype view="statements" />;
}
