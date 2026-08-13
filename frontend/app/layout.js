import "./globals.css";
import Script from "next/script";

export const metadata = {
  title: "PromptRail",
  description:
    "Set your AI budget and route agent model spend where it matters.",
  metadataBase: new URL(process.env.NEXT_PUBLIC_APP_URL || "https://www.promptrail.ai"),
  icons: {
    icon: "/icon.png",
    shortcut: "/icon.png",
    apple: "/icon.png",
  },
};

export const viewport = {
  width: "device-width",
  initialScale: 1,
};

const datafastWebsiteId = process.env.DATAFAST_WEBSITE_ID;

export default function RootLayout({ children }) {
  return (
    <html lang="en" data-scroll-behavior="smooth">
      <body>
        {process.env.NODE_ENV === "production" && datafastWebsiteId ? (
          <Script
            src="https://datafa.st/js/script.js"
            data-website-id={datafastWebsiteId}
            data-domain="promptrail.ai"
            strategy="lazyOnload"
          />
        ) : null}
        {children}
      </body>
    </html>
  );
}
