import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "DSE Pipeline Management",
  description: "DSE Stock Intelligence Platform — Pipeline Management UI",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body className="bg-gray-950 text-gray-100 min-h-screen font-mono">
        <nav className="border-b border-gray-800 px-6 py-3 flex gap-6 text-sm">
          <span className="text-green-400 font-bold">DSE Pipeline</span>
          <a href="/dashboard" className="text-gray-400 hover:text-white">Dashboard</a>
          <a href="/streams" className="text-gray-400 hover:text-white">Streams</a>
          <a href="/alerts" className="text-gray-400 hover:text-white">Alerts</a>
          <a href="/agent" className="text-gray-400 hover:text-white">Agent</a>
        </nav>
        <main className="p-6">{children}</main>
      </body>
    </html>
  );
}
