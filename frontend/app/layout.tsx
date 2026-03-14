import type { Metadata } from "next";
import { IBM_Plex_Sans, Space_Grotesk } from "next/font/google";

import "./globals.css";

const bodyFont = IBM_Plex_Sans({
  subsets: ["latin"],
  weight: ["400", "500", "600"]
});

const headingFont = Space_Grotesk({
  subsets: ["latin"],
  weight: ["500", "700"],
  variable: "--font-space-grotesk"
});

export const metadata: Metadata = {
  title: "NSE AI Swing Scanner",
  description: "Systematic swing-trading scanner MVP for NSE stocks."
};

export default function RootLayout({
  children
}: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="en">
      <body
        className={`${bodyFont.className} ${headingFont.variable}`}
      >
        {children}
      </body>
    </html>
  );
}
