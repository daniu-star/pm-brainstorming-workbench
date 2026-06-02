import type { Metadata } from "next";

export const metadata: Metadata = {
  robots: "noindex",
};

export default function InterviewLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return children;
}
