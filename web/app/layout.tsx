import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "Flightcraft",
  description:
    "Scan whole months of fares, then filter by stops, airline, carrier type and aircraft.",
};

export default function RootLayout({
  children,
}: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="en">
      <body className="min-h-screen antialiased">{children}</body>
    </html>
  );
}
