import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "RemixKit — UGC-inspiring content for every artist on your roster",
  description:
    "For labels and artist managers. Build each artist's on-screen identity once, then generate provenance-clean, disclosure-ready content for every song in the catalogue — at a cost per release that does not move.",
  openGraph: {
    title: "RemixKit — more releases beat more spend",
    description:
      "Generate UGC-inspiring content for every artist on your roster. Provenance embedded in every file. Built on Genblaze and Backblaze B2.",
    type: "website",
  },
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body className="antialiased">{children}</body>
    </html>
  );
}
