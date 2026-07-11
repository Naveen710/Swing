import type { Metadata } from "next";

import "./globals.css";

export const metadata: Metadata = {
  title: "NSE AI Swing Scanner",
  description: "Systematic swing-trading scanner MVP for NSE stocks."
};

export default function RootLayout({
  children
}: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="en">
      <body>{children}</body>
    </html>
  );
}
