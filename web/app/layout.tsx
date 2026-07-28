import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "FaceAu",
  description: "Biometric attendance and employee authentication console"
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body>{children}</body>
    </html>
  );
}
