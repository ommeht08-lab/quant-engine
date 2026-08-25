import type { Metadata } from "next";
import type { ReactNode } from "react";
import { IBM_Plex_Mono, IBM_Plex_Sans, Instrument_Sans } from "next/font/google";
import AppHeader from "@/components/AppHeader";
import "./globals.css";

// Display: used sparingly (section headings, the workspace title) — never
// for financial data. Body: copy and labels. Mono: every dollar amount,
// percent, and ticker, so figures stay tabular and legible.
const instrumentSans = Instrument_Sans({
  subsets: ["latin"],
  weight: ["500", "600", "700"],
  variable: "--font-instrument-sans",
  display: "swap",
});

const plexSans = IBM_Plex_Sans({
  subsets: ["latin"],
  weight: ["400", "500", "600"],
  variable: "--font-plex-sans",
  display: "swap",
});

const plexMono = IBM_Plex_Mono({
  subsets: ["latin"],
  weight: ["400", "500", "600"],
  variable: "--font-plex-mono",
  display: "swap",
});

export const metadata: Metadata = {
  title: "Valuation Engine | Om Mehta Equity Research",
  description: "A private intrinsic-value and paper-portfolio research workspace.",
};

export default function RootLayout({ children }: { children: ReactNode }) {
  return (
    <html
      lang="en"
      className={`h-full antialiased ${instrumentSans.variable} ${plexSans.variable} ${plexMono.variable}`}
    >
      <body className="min-h-full">
        <AppHeader />
        {children}
      </body>
    </html>
  );
}
