import ProductDashboard from "../../components/ProductDashboard";

export const metadata = {
  title: "PromptRail Settings",
  description: "Account settings for PromptRail.",
};

export default function DashboardSettingsPage() {
  return <ProductDashboard activePage="settings" />;
}
