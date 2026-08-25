import type { Metadata } from "next";
import { Geist, Geist_Mono } from "next/font/google";
import "./globals.css";

const sans = Geist({ variable: "--font-sans", subsets: ["latin"] });
const mono = Geist_Mono({ variable: "--font-mono", subsets: ["latin"] });

export const metadata: Metadata = {
  metadataBase: new URL("https://labs.ratifyprotocol.com"),
  title: "Maritime × Ratify — Live Authorization Lab",
  description: "See delegated authority allow or deny a real Maritime agent work order before protected code runs.",
  icons: { icon: "/maritime/favicon.svg" },
  openGraph: { title: "Maritime × Ratify", description: "An agent can ask. Authority decides.", type: "website", images: ["/maritime/og.jpg"] },
  twitter: { card: "summary_large_image", title: "Maritime × Ratify", description: "An agent can ask. Authority decides.", images: ["/maritime/og.jpg"] },
};

export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return <html lang="en"><body className={`${sans.variable} ${mono.variable}`}>{children}</body></html>;
}
