import ProductDashboard from "../../components/ProductDashboard";

export const metadata = {
  title: "PromptRail Setup",
  description: "Copy PromptRail setup prompts for Hermes or OpenClaw.",
};

export default function DashboardSetupPage() {
  return <ProductDashboard activePage="setup" />;
}
