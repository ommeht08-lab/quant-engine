import type { Metadata } from "next";
import type { ReactNode } from "react";
import AppHeader from "@/components/AppHeader";
import "./globals.css";

export const metadata: Metadata = {
  title: "Valuation Engine | Om Mehta Equity Research",
  description: "A private intrinsic-value and paper-portfolio research workspace.",
};

export default function RootLayout({ children }: { children: ReactNode }) {
  return (
    <html lang="en" className="h-full antialiased">
      <body className="min-h-full">
        <AppHeader />
        {children}
      </body>
    </html>
  );
}
