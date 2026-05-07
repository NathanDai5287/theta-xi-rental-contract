import type { Metadata } from "next";
import "./globals.css";
import Nav from "@/components/Nav";
import { SharedDataProvider } from "@/lib/shared-state";

export const metadata: Metadata = {
  title: "Theta Xi Rental Tools",
  description: "Generate contracts, price estimates, and invoices for Theta Xi Fraternity rentals.",
};

export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <html lang="en">
      <body>
        <SharedDataProvider>
          <Nav />
          <main className="max-w-[1080px] mx-auto px-6 py-8">
            {children}
          </main>
        </SharedDataProvider>
      </body>
    </html>
  );
}
