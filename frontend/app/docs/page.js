import { redirect } from "next/navigation";

export const metadata = {
  title: "PromptRail Documentation",
  description: "PromptRail SDK, runtime, routing, and trace connection documentation.",
};

export default function DocumentationIndexPage() {
  redirect("/docs/sdk");
}
