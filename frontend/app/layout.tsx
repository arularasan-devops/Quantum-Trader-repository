import "./globals.css";
import type { Metadata } from "next";

export const metadata: Metadata = {
  title: "Quantum Trader — MCX Crude Decision Engine",
  description: "Institutional-grade decision engine for MCX Crude Oil options",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body className="font-mono antialiased">{children}</body>
    </html>
  );
}
